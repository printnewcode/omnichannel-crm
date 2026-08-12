import hashlib
import hmac
import json
import time
from typing import Optional

import requests

from ..models import Chat, Message, TelegramAccount


SIGNATURE_HEADER = 'X-CRM-Signature'
TIMESTAMP_HEADER = 'X-CRM-Timestamp'
MAX_SIGNATURE_AGE_SECONDS = 300


def build_signature(secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode('utf-8') + b'.' + body
    digest = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > MAX_SIGNATURE_AGE_SECONDS:
            return False
    except (TypeError, ValueError):
        return False

    expected = build_signature(secret, timestamp, body)
    return hmac.compare_digest(expected, signature or '')


def _question_id(message: Optional[Message] = None, chat: Optional[Chat] = None) -> Optional[int]:
    if message:
        value = (message.metadata or {}).get('question_id')
        if value:
            return int(value)
        chat = message.chat

    value = ((chat.metadata or {}).get('jget') or {}).get('question_id') if chat else None
    return int(value) if value else None


def send_reply(
    account: TelegramAccount,
    text: str,
    *,
    message: Optional[Message] = None,
    chat: Optional[Chat] = None,
) -> Optional[int]:
    question_id = _question_id(message=message, chat=chat)
    if not account.bridge_url or not account.bridge_secret or not question_id:
        return None

    url = account.bridge_url.format(question_id=question_id)
    payload = {
        'text': text,
        'question_id': question_id,
        'crm_message_id': message.id if message else None,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    timestamp = str(int(time.time()))
    response = requests.post(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: build_signature(account.bridge_secret, timestamp, body),
        },
        timeout=10,
    )
    response.raise_for_status()
    result = response.json()
    telegram_message_id = result.get('telegram_message_id')
    return int(telegram_message_id) if telegram_message_id else None
