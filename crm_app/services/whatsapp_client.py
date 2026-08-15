"""Shared GREEN-API client for personal WhatsApp and MAX accounts."""

from pathlib import Path
import random
import time

import requests
from django.conf import settings


class GreenAPIError(RuntimeError):
    pass


class GreenAPIClient:
    def __init__(self, account, *, session=None, timeout=30):
        if not account.green_api_instance_id or not account.green_api_token:
            raise GreenAPIError('GREEN-API idInstance or apiTokenInstance is missing')
        self.account = account
        self.session = session or requests.Session()
        self.timeout = timeout
        self.api_url = (account.green_api_url or 'https://api.green-api.com').rstrip('/')
        self.media_url = (account.green_media_url or 'https://media.green-api.com').rstrip('/')
        self._last_request_at = {}

    def _wait_for_rate_slot(self, api_method):
        """Respect GREEN-API's one-request-per-second journal limits."""
        intervals = {
            'getChatHistory': 1.1,
            'getChats': 1.1,
            'lastIncomingMessages': 1.1,
            'lastOutgoingMessages': 1.1,
        }
        interval = intervals.get(api_method)
        if not interval:
            return
        elapsed = time.monotonic() - self._last_request_at.get(api_method, 0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at[api_method] = time.monotonic()

    def _url(self, method, *, media=False):
        host = self.media_url if media else self.api_url
        return f'{host}/waInstance{self.account.green_api_instance_id}/{method}/{self.account.green_api_token}'

    def _request(self, method, api_method, *, media=False, **kwargs):
        timeout = kwargs.pop('timeout', self.timeout)
        response = None
        for attempt in range(4):
            self._wait_for_rate_slot(api_method)
            response = self.session.request(method, self._url(api_method, media=media), timeout=timeout, **kwargs)
            if response.ok:
                try:
                    return response.json()
                except ValueError as exc:
                    raise GreenAPIError('GREEN-API returned invalid JSON') from exc
            if response.status_code != 429 or attempt == 3:
                break
            retry_after = response.headers.get('Retry-After')
            try:
                delay = max(float(retry_after), 1.1) if retry_after else 1.5 * (2 ** attempt)
            except (TypeError, ValueError):
                delay = 1.5 * (2 ** attempt)
            time.sleep(min(delay + random.uniform(0.05, 0.25), 10))
        try:
            payload = response.json()
            detail = payload.get('message') or payload.get('error') or payload
        except ValueError:
            detail = response.text[:500]
        raise GreenAPIError(f'GREEN-API HTTP {response.status_code}: {detail}')

    def normalize_chat_id(self, chat_id):
        value = str(chat_id).strip()
        if self.account.account_type == 'max':
            return value
        value = value.lower()
        return value if '@' in value else f'{value.lstrip("+")}@c.us'

    @staticmethod
    def _message_id(data):
        message_id = data.get('idMessage')
        if not message_id:
            raise GreenAPIError('GREEN-API did not return idMessage')
        return str(message_id)

    def send_text(self, chat_id, text, *, quoted_message_id=None):
        if not text:
            raise GreenAPIError('Message text is empty')
        payload = {'chatId': self.normalize_chat_id(chat_id), 'message': text}
        if quoted_message_id:
            payload['quotedMessageId'] = str(quoted_message_id)
        return self._message_id(self._request('POST', 'sendMessage', json=payload))

    def _local_file(self, media_path):
        root = Path(settings.MEDIA_ROOT).resolve()
        candidate = Path(media_path)
        path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if path != root and root not in path.parents:
            raise GreenAPIError('Media path is outside MEDIA_ROOT')
        if not path.is_file():
            raise GreenAPIError('Media file does not exist')
        if path.stat().st_size > 100 * 1024 * 1024:
            raise GreenAPIError('GREEN-API media exceeds the 100 MB limit')
        return path

    def send_file(self, chat_id, media_path, *, caption='', quoted_message_id=None):
        path = self._local_file(media_path)
        data = {'chatId': self.normalize_chat_id(chat_id), 'fileName': path.name}
        if caption:
            limit = 4000 if self.account.account_type == 'max' else 1024
            data['caption'] = caption[:limit]
        if quoted_message_id:
            data['quotedMessageId'] = str(quoted_message_id)
        with path.open('rb') as stream:
            result = self._request(
                'POST', 'sendFileByUpload', media=True, data=data,
                files={'file': (path.name, stream)}, timeout=max(self.timeout, 120),
            )
        return self._message_id(result)

    def send(self, chat_id, text='', media_path=None, *, reply_to_id=None):
        if media_path:
            return self.send_file(chat_id, media_path, caption=text, quoted_message_id=reply_to_id)
        return self.send_text(chat_id, text, quoted_message_id=reply_to_id)

    def get_settings(self):
        return self._request('GET', 'getSettings')

    def get_chats(self, count=1000):
        # MAX GetChats has no count query parameter, unlike WhatsApp GetChats.
        if self.account.account_type == 'max':
            return self._request('GET', 'getChats')
        return self._request('GET', 'getChats', params={'count': max(1, min(int(count), 1000))})

    def get_chat_history(self, chat_id, count=100):
        maximum = 5000 if self.account.account_type == 'max' else 10000
        payload = {
            'chatId': self.normalize_chat_id(chat_id),
            'count': max(1, min(int(count), maximum)),
        }
        return self._request('POST', 'getChatHistory', json=payload)

    def get_last_incoming_messages(self, minutes):
        return self._request(
            'GET', 'lastIncomingMessages',
            params={'minutes': max(1, int(minutes))},
        )

    def get_last_outgoing_messages(self, minutes):
        return self._request(
            'GET', 'lastOutgoingMessages',
            params={'minutes': max(1, int(minutes))},
        )

    def configure_webhook(self, webhook_url):
        token = (self.account.green_webhook_token or '').strip()
        if token and not token.lower().startswith(('bearer ', 'basic ')):
            token = f'Bearer {token}'
        return self._request('POST', 'setSettings', json={
            'webhookUrl': webhook_url,
            'webhookUrlToken': token,
            'incomingWebhook': 'yes',
            'outgoingWebhook': 'yes',
            'outgoingAPIMessageWebhook': 'yes',
            'stateWebhook': 'yes',
        })
