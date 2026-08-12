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
    if re.fullmatch(r'(?:sw-)?media-[0-9]+\.storage\.yandexcloud\.net', hostname):
        return True
    return False


def download_green_api_media(message):
    account = message.chat.telegram_account
    url = (message.metadata or {}).get('download_url')
    if not url:
        return None
    parsed = urlparse(url)
    if (
        parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
        or parsed.port not in (None, 443)
        or not _allowed_green_api_host(parsed.hostname, account)
    ):
        raise ValueError('Unsafe GREEN-API media URL')

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