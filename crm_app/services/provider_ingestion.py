"""Normalize webhook-based provider messages into the existing CRM models."""

import hashlib

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Chat, Message
from .realtime import publish_message



@transaction.atomic
def ingest_provider_message(
    *,
    account,
    external_chat_id,
    external_message_id,
    text='',
    sender_id=None,
    sender_name=None,
    username=None,
    occurred_at=None,
    message_type=Message.MessageType.TEXT,
    media_file_id=None,
    reply_to_external_message_id=None,
    metadata=None,
    chat_type=Chat.ChatType.PRIVATE,
    is_bot=False,
    is_outgoing=False,
    status=Message.MessageStatus.RECEIVED,
    publish=True,
):
    raw_chat_id = str(external_chat_id)
    base_chat_id = raw_chat_id.split('@', 1)[0]
    if base_chat_id.isdigit():
        chat_id = int(base_chat_id)
    else:
        chat_id = int.from_bytes(hashlib.sha256(raw_chat_id.encode()).digest()[:7], 'big')
    chat, chat_created = Chat.objects.get_or_create(
        telegram_id=chat_id,
        telegram_account=account,
        defaults={
            'chat_type': chat_type,
            'title': sender_name or str(external_chat_id),
            'username': username,
            'first_name': sender_name,
            'metadata': {'provider': account.account_type, 'external_chat_id': raw_chat_id},
            'is_bot': bool(is_bot),
        },
    )
    chat_updates = []
    if sender_name and chat.title != sender_name:
        chat.title = sender_name
        chat_updates.append('title')
    if chat.chat_type != chat_type:
        chat.chat_type = chat_type
        chat_updates.append('chat_type')
    if chat.is_bot != bool(is_bot):
        chat.is_bot = bool(is_bot)
        chat_updates.append('is_bot')
    if chat_updates:
        chat.save(update_fields=[*chat_updates, 'updated_at'])

    event_time = occurred_at or timezone.now()
    reply_to_message = None
    if reply_to_external_message_id:
        reply_to_message = Message.objects.filter(
            chat=chat, external_message_id=str(reply_to_external_message_id)
        ).first()

    message, created = Message.objects.get_or_create(
        chat=chat,
        external_message_id=str(external_message_id),
        defaults={
            'telegram_id': None,
            'text': text or None,
            'message_type': message_type,
            'status': status,
            'from_user_id': (
                int(str(sender_id).split('@', 1)[0])
                if sender_id and str(sender_id).split('@', 1)[0].isdigit() else None
            ),
            'from_user_name': sender_name,
            'from_user_username': username,
            'is_outgoing': is_outgoing,
            'telegram_date': event_time,
            'media_file_id': media_file_id,
            'reply_to_message': reply_to_message,
            'metadata': {'provider': account.account_type, **(metadata or {})},
        },
    )
    if created:
        Chat.objects.filter(pk=chat.pk).update(
            message_count=F('message_count') + 1,
            unread_count=F('unread_count') + (0 if is_outgoing else 1),
            last_message_at=event_time,
        )

    if publish:
        transaction.on_commit(lambda message_id=message.id: publish_message(message_id))
    return message, created, chat_created
