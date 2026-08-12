from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Chat, Message, TelegramAccount
from .realtime import publish_message


def _parse_date(value):
    if not value:
        return timezone.now()
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed



@transaction.atomic
def ingest_question(account: TelegramAccount, payload: dict):
    required = ('question_id', 'telegram_message_id', 'telegram_chat_id', 'text')
    missing = [field for field in required if payload.get(field) in (None, '')]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    user = payload.get('user') or {}
    chat, chat_created = Chat.objects.get_or_create(
        telegram_id=int(payload['telegram_chat_id']),
        telegram_account=account,
        defaults={
            'chat_type': Chat.ChatType.PRIVATE,
            'title': user.get('name') or f"User {payload['telegram_chat_id']}",
            'username': user.get('username'),
            'first_name': user.get('first_name'),
            'last_name': user.get('last_name'),
            'metadata': {},
        },
    )

    chat_metadata = dict(chat.metadata or {})
    chat_metadata['jget'] = {
        'question_id': int(payload['question_id']),
        'city_id': user.get('city_id'),
        'role': user.get('role'),
    }
    chat.metadata = chat_metadata
    if user.get('name'):
        chat.title = user['name']
    if user.get('username'):
        chat.username = user['username']
    chat.save(update_fields=['metadata', 'title', 'username', 'updated_at'])

    message, message_created = Message.objects.get_or_create(
        telegram_id=int(payload['telegram_message_id']),
        chat=chat,
        defaults={
            'message_type': Message.MessageType.TEXT,
            'status': Message.MessageStatus.RECEIVED,
            'text': payload['text'],
            'from_user_id': int(payload.get('telegram_user_id') or payload['telegram_chat_id']),
            'from_user_name': user.get('name'),
            'from_user_username': user.get('username'),
            'is_outgoing': False,
            'telegram_date': _parse_date(payload.get('created_at')),
            'metadata': {
                'source': 'jget_question',
                'event_id': payload.get('event_id'),
                'question_id': int(payload['question_id']),
            },
        },
    )

    if message_created:
        Chat.objects.filter(pk=chat.pk).update(
            message_count=F('message_count') + 1,
            unread_count=F('unread_count') + 1,
            last_message_at=message.telegram_date,
        )

    transaction.on_commit(lambda message_id=message.id: publish_message(message_id))
    return message, message_created, chat_created
