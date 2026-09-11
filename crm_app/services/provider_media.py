"""Safe media download helpers for webhook-based providers."""

import base64
import binascii
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.utils.text import get_valid_filename

MAX_GREEN_API_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_GREEN_API_THUMBNAIL_BYTES = 5 * 1024 * 1024


def _allowed_green_api_host(hostname, account):
    hostname = (hostname or '').lower().rstrip('.')
    configured = {
        (urlparse(account.green_api_url or '').hostname or '').lower().rstrip('.'),
        (urlparse(account.green_media_url or '').hostname or '').lower().rstrip('.'),
    }
    if hostname in configured or any(
        hostname == suffix or hostname.endswith('.' + suffix)
        for suffix in ('green-api.com', 'greenapi.com')
    ):
        return True
    # GREEN-API signs incoming media links on provider-owned object-storage clusters.
    if re.fullmatch(r'do-media-[0-9]+\.[a-z0-9-]+\.digitaloceanspaces\.com', hostname):
        return True
    # Official GREEN-API WhatsApp webhooks use several CDN subdomains under
    # this provider-owned storage zone. The exact prefix can vary by cluster.
    if hostname == 'storage.greenapi.net' or hostname.endswith('.storage.greenapi.net'):
        return True
    # MAX and WhatsApp notifications currently use these provider storage forms.
    # Some WhatsApp clusters are served from the Kazakhstan Yandex Cloud zone.
    # sw-media.storage..., sw-media-3100.storage... and (for uploaded/outgoing
    # files) sw-media-out.storage.... Older instances may omit the sw- prefix.
    if re.fullmatch(
        r'(?:sw-media(?:-[0-9]+|-out)?|media-[0-9]+)\.storage\.yandexcloud\.(?:net|kz)',
        hostname,
    ):
        return True
    return False


def _green_media_info(payload):
    """Normalize webhook, journal and getMessage media shapes."""
    if not isinstance(payload, dict):
        return {}, []
    message_data = payload.get('messageData') if isinstance(payload.get('messageData'), dict) else payload
    content = message_data.get('fileMessageData') or message_data.get('stickerMessageData') or message_data
    if not isinstance(content, dict):
        content = {}
    urls = []
    for source in (content, message_data, payload):
        if not isinstance(source, dict):
            continue
        for key in ('downloadUrl', 'downloadUrlJpeg', 'urlFile'):
            value = source.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in urls:
                urls.append(value.strip())
    return content, urls


def _store_green_thumbnail(message, metadata, content):
    """Use the provider preview only when the original image has expired."""
    if message.message_type not in {message.MessageType.PHOTO, message.MessageType.STICKER}:
        return None
    thumbnail = content.get('jpegThumbnail') if isinstance(content, dict) else None
    if not isinstance(thumbnail, str) or not thumbnail.strip():
        return None
    encoded = thumbnail.split(',', 1)[-1].strip()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_GREEN_API_THUMBNAIL_BYTES:
        return None

    relative = Path(message.chat.telegram_account.account_type) / 'preview' / str(message.id) / f'preview_{message.id}.jpg'
    root = Path(settings.MEDIA_ROOT).resolve()
    destination = (root / relative).resolve()
    if root not in destination.parents:
        raise ValueError('Unsafe GREEN-API media destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    message.media_file_path = relative.as_posix()
    message.metadata = {
        **metadata,
        'provider_content': content,
        'media_content_type': 'image/jpeg',
        'media_size': len(raw),
        'original_filename': f'preview_{message.id}.jpg',
        'media_is_preview': True,
    }
    message.save(update_fields=['media_file_path', 'metadata', 'updated_at'])
    return message.media_file_path


def download_green_api_media(message):
    account = message.chat.telegram_account
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    chat_metadata = message.chat.metadata if isinstance(message.chat.metadata, dict) else {}
    url = metadata.get('download_url')
    parsed = urlparse(url or '')

    def is_safe(candidate):
        try:
            port = candidate.port
        except ValueError:
            return False
        return bool(
            candidate.scheme == 'https' and candidate.hostname
            and not candidate.username and not candidate.password
            and port in (None, 443)
            and _allowed_green_api_host(candidate.hostname, account)
        )

    from .whatsapp_client import GreenAPIClient, GreenAPIError

    external_chat_id = metadata.get('external_chat_id') or chat_metadata.get('external_chat_id') or message.chat.telegram_id
    external_message_id = message.external_message_id or message.media_file_id or message.telegram_id
    candidates = [url] if isinstance(url, str) and url.strip() else []
    content = metadata.get('provider_content') if isinstance(metadata.get('provider_content'), dict) else {}
    provider_errors = []
    response = None
    safe_candidates = []

    def try_candidate(candidate):
        nonlocal response, url
        candidate_parsed = urlparse(candidate or '')
        if not is_safe(candidate_parsed):
            return
        safe_candidates.append(candidate)
        try:
            attempted = requests.get(candidate, timeout=60, stream=True)
            attempted.raise_for_status()
            response = attempted
            url = candidate
        except requests.RequestException as exc:
            provider_errors.append(str(exc))

    # Webhooks often already contain a working direct storage link. Avoid an
    # extra provider request for fresh messages and only recover when it fails.
    for candidate in list(candidates):
        try_candidate(candidate)
        if response is not None:
            break

    # getMessage is the most precise recovery method for old journal entries:
    # it can return a refreshed URL or at least a JPEG preview.
    if response is None and external_chat_id and external_message_id:
        client = GreenAPIClient(account)
        try:
            provider_message = client.get_message(external_chat_id, external_message_id)
            fresh_content, fresh_urls = _green_media_info(provider_message)
            if fresh_content:
                content = fresh_content
            for candidate in fresh_urls:
                if candidate not in candidates:
                    candidates.append(candidate)
        except GreenAPIError as exc:
            provider_errors.append(str(exc))

        for candidate in candidates:
            if candidate in safe_candidates:
                continue
            try_candidate(candidate)
            if response is not None:
                break

        # downloadFile is the last resort. GREEN-API documents that it returns
        # HTTP 400 when WhatsApp no longer exposes the encrypted media URL.
        if response is None:
            try:
                refreshed_url = client.get_download_url(external_chat_id, external_message_id)
                if refreshed_url and refreshed_url not in candidates:
                    candidates.append(refreshed_url)
                    try_candidate(refreshed_url)
            except GreenAPIError as exc:
                provider_errors.append(str(exc))

    if response is None:
        preview_path = _store_green_thumbnail(message, metadata, content)
        if preview_path:
            return preview_path
        if candidates and not safe_candidates:
            host_label = urlparse(candidates[0] or '').hostname or 'missing host'
            raise ValueError(f'Unsafe GREEN-API media URL (host: {host_label})')
        raise ValueError(
            'Оригинал файла больше недоступен в WhatsApp/GREEN-API. '
            'Провайдер ограничивает срок хранения старых вложений.'
        )

    metadata = {**metadata, 'download_url': url, 'provider_content': content}
    declared_size = int(response.headers.get('Content-Length') or 0)
    if declared_size > MAX_GREEN_API_DOWNLOAD_BYTES:
        raise ValueError('GREEN-API media exceeds the 100 MB download limit')

    content_type = response.headers.get('Content-Type') or content.get('mimeType') or 'application/octet-stream'
    content_type = content_type.split(';')[0]
    supplied_name = Path(content.get('fileName') or '').name
    extension = Path(supplied_name).suffix or mimetypes.guess_extension(content_type) or '.bin'
    original_name = get_valid_filename(supplied_name) if supplied_name else ''
    if not original_name:
        original_name = f'{message.message_type}_{message.id}{extension}'
    relative = Path(account.account_type) / message.message_type / str(message.id) / original_name
    root = Path(settings.MEDIA_ROOT).resolve()
    destination = (root / relative).resolve()
    if root not in destination.parents:
        raise ValueError('Unsafe GREEN-API media destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.part')

    written = 0
    try:
        with temporary.open('wb') as target:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_GREEN_API_DOWNLOAD_BYTES:
                    raise ValueError('GREEN-API media exceeds the 100 MB download limit')
                target.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    message.media_file_path = relative.as_posix()
    message.metadata = {**metadata, 'media_content_type': content_type, 'media_size': written, 'original_filename': original_name}
    message.save(update_fields=['media_file_path', 'metadata', 'updated_at'])
    return message.media_file_path
