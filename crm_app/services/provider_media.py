"""Safe media download helpers for webhook-based providers."""

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.utils.text import get_valid_filename

MAX_GREEN_API_DOWNLOAD_BYTES = 100 * 1024 * 1024


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
    # MAX notifications currently use all of these official storage forms:
    # sw-media.storage..., sw-media-3100.storage... and (for uploaded/outgoing
    # files) sw-media-out.storage.... Older instances may omit the sw- prefix.
    if re.fullmatch(
        r'(?:sw-media(?:-[0-9]+|-out)?|media-[0-9]+)\.storage\.yandexcloud\.net',
        hostname,
    ):
        return True
    return False


def download_green_api_media(message):
    account = message.chat.telegram_account
    url = (message.metadata or {}).get('download_url')
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

    # Journal entries and older webhooks can contain an empty, expired or
    # cluster-specific URL. Ask GREEN-API for a fresh official link before
    # rejecting the attachment. This works for both WhatsApp and MAX.
    if not is_safe(parsed):
        from .whatsapp_client import GreenAPIClient

        metadata = message.metadata or {}
        external_chat_id = metadata.get('external_chat_id') or (message.chat.metadata or {}).get('external_chat_id') or message.chat.telegram_id
        external_message_id = message.external_message_id or message.media_file_id or message.telegram_id
        refreshed_url = None
        if external_chat_id and external_message_id:
            refreshed_url = GreenAPIClient(account).get_download_url(external_chat_id, external_message_id)
        if refreshed_url:
            url = refreshed_url
            parsed = urlparse(url)
            message.metadata = {**metadata, 'download_url': url}
            message.save(update_fields=['metadata', 'updated_at'])

    if not is_safe(parsed):
        host_label = parsed.hostname or 'missing host'
        raise ValueError(f'Unsafe GREEN-API media URL (host: {host_label})')

    response = requests.get(url, timeout=60, stream=True)
    response.raise_for_status()
    declared_size = int(response.headers.get('Content-Length') or 0)
    if declared_size > MAX_GREEN_API_DOWNLOAD_BYTES:
        raise ValueError('GREEN-API media exceeds the 100 MB download limit')

    content = (message.metadata or {}).get('provider_content') or {}
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
    message.metadata = {**(message.metadata or {}), 'media_content_type': content_type, 'media_size': written, 'original_filename': original_name}
    message.save(update_fields=['media_file_path', 'metadata', 'updated_at'])
    return message.media_file_path
