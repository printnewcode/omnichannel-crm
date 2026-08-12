"""Shared GREEN-API client for personal WhatsApp and MAX accounts."""

from pathlib import Path

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

    def _url(self, method, *, media=False):
        host = self.media_url if media else self.api_url
        return f'{host}/waInstance{self.account.green_api_instance_id}/{method}/{self.account.green_api_token}'

    def _request(self, method, api_method, *, media=False, **kwargs):
        timeout = kwargs.pop('timeout', self.timeout)
        response = self.session.request(method, self._url(api_method, media=media), timeout=timeout, **kwargs)
        if response.ok:
            try:
                return response.json()
            except ValueError as exc:
                raise GreenAPIError('GREEN-API returned invalid JSON') from exc
        try:
            payload = response.json()
            detail = payload.get('message') or payload.get('error') or payload
        except ValueError:
            detail = response.text[:500]
        raise GreenAPIError(f'GREEN-API HTTP {response.status_code}: {detail}')

    def normalize_chat_id(self, chat_id):
        value = str(chat_id)
        if self.account.account_type == 'max':
            return value
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

