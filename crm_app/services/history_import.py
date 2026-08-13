"""Bounded, provider-neutral history import helpers."""

import asyncio
from datetime import datetime, timezone as dt_timezone

from django.db.models import Max
from django.utils import timezone
from channels.db import database_sync_to_async
from telethon.sessions import StringSession

from ..models import Chat, HistoryImportJob, Message, TelegramAccount
from .provider_ingestion import ingest_provider_message
from .telegram_client_manager import TelegramClientManager
from .whatsapp_client import GreenAPIClient


MEDIA_TYPES = {
    'imageMessage': Message.MessageType.PHOTO,
    'videoMessage': Message.MessageType.VIDEO,
    'audioMessage': Message.MessageType.VOICE,
    'documentMessage': Message.MessageType.DOCUMENT,
    'stickerMessage': Message.MessageType.STICKER,
}


def _green_content(item):
    raw_type = item.get('typeMessage') or 'textMessage'
    text = item.get('textMessage') or (item.get('extendedTextMessage') or {}).get('text') or ''
    content = item.get('fileMessageData') or item.get('stickerMessageData') or item
    download_url = item.get('downloadUrl') or content.get('downloadUrl')
    return raw_type, text or content.get('caption') or '', content, download_url


def _green_datetime(value):
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError):
        return timezone.now()


def _ingest_green_items(chat, items):
    """Persist a history response already fetched from GREEN-API."""
    external_chat_id = (chat.metadata or {}).get('external_chat_id') or chat.telegram_id
    created = 0
    media_ids = []
    for item in reversed(items if isinstance(items, list) else []):
        external_message_id = item.get('idMessage')
        if not external_message_id:
            continue
        raw_type, text, content, download_url = _green_content(item)
        quoted = item.get('quotedMessage') or item.get('contextInfo') or {}
        message, was_created, _ = ingest_provider_message(
            account=chat.telegram_account,
            external_chat_id=external_chat_id,
            external_message_id=external_message_id,
            text=text,
            sender_id=item.get('senderId'),
            sender_name=item.get('senderName') or item.get('senderContactName') or chat.title,
            occurred_at=_green_datetime(item.get('timestamp')),
            message_type=MEDIA_TYPES.get(raw_type, Message.MessageType.TEXT),
            media_file_id=external_message_id if download_url else None,
            reply_to_external_message_id=quoted.get('idMessage') or quoted.get('stanzaId'),
            metadata={'raw_type': raw_type, 'provider_content': content, 'download_url': download_url, 'external_chat_id': str(external_chat_id)},
            chat_type=Chat.ChatType.PRIVATE,
            is_outgoing=item.get('type') == 'outgoing',
            status=Message.MessageStatus.SENT if item.get('type') == 'outgoing' else Message.MessageStatus.RECEIVED,
            publish=False,
        )
        if was_created:
            created += 1
            if download_url and message.message_type == Message.MessageType.STICKER:
                media_ids.append(message.id)
    return created, len(items) if isinstance(items, list) else 0, media_ids


def import_green_history(chat, count):
    client = GreenAPIClient(chat.telegram_account)
    external_chat_id = (chat.metadata or {}).get('external_chat_id') or chat.telegram_id
    items = client.get_chat_history(external_chat_id, count=count)
    return _ingest_green_items(chat, items)


def _persist_telegram_message(chat, item, manager):
    reply = None
    if item.reply_to_msg_id:
        reply = Message.objects.filter(chat=chat, telegram_id=item.reply_to_msg_id).first()
    return Message.objects.get_or_create(
        chat=chat,
        telegram_id=item.id,
        defaults={
            'text': item.message or None,
            'message_type': manager._get_message_type(item),
            'status': Message.MessageStatus.SENT if item.out else Message.MessageStatus.RECEIVED,
            'is_outgoing': item.out,
            'telegram_date': item.date,
            'reply_to_message': reply,
            'media_caption': item.message if item.media else None,
        },
    )


def _repair_telegram_replies(chat, pending_replies):
    """Link replies after newest-first Telegram history has been persisted."""
    if not pending_replies:
        return
    target_ids = {reply_id for _, reply_id in pending_replies}
    targets = {
        message.telegram_id: message
        for message in Message.objects.filter(chat=chat, telegram_id__in=target_ids)
    }
    messages = {
        message.id: message
        for message in Message.objects.filter(pk__in=[message_id for message_id, _ in pending_replies])
    }
    updates = []
    for message_id, reply_id in pending_replies:
        message = messages.get(message_id)
        target = targets.get(reply_id)
        if message and target and message.reply_to_message_id != target.id:
            message.reply_to_message = target
            updates.append(message)
    if updates:
        Message.objects.bulk_update(updates, ['reply_to_message'])


def _refresh_chat_stats(chat):
    aggregate = Message.objects.filter(chat=chat).aggregate(last=Max('telegram_date'))
    chat.message_count = Message.objects.filter(chat=chat).count()
    chat.last_message_at = aggregate['last']
    chat.save(update_fields=['message_count', 'last_message_at', 'updated_at'])


async def _import_telegram_messages(client, entity, chat, count, manager):
    created = processed = 0
    pending_replies = []
    async for item in client.iter_messages(entity, limit=count):
        processed += 1
        if not item.message and not item.media:
            continue
        message, was_created = await database_sync_to_async(_persist_telegram_message)(chat, item, manager)
        if was_created:
            created += 1
            if item.reply_to_msg_id and not message.reply_to_message_id:
                pending_replies.append((message.id, item.reply_to_msg_id))
            if message.message_type == Message.MessageType.STICKER:
                await manager._download_media_telethon(client, item, message)
    await database_sync_to_async(_repair_telegram_replies)(chat, pending_replies)
    if created:
        await database_sync_to_async(_refresh_chat_stats)(chat)
    return created, processed


async def _import_telegram_history(chat, account, count):
    # ``account`` is resolved before entering asyncio. Accessing the related
    # Django object through ``chat.telegram_account`` here may trigger a lazy
    # synchronous query and Django correctly rejects that in an async context.
    manager = TelegramClientManager()
    client = manager._create_client(StringSession(account.session_string), account.api_id, account.api_hash)
    created = 0
    processed = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError('Сессия Telegram больше не авторизована.')
        entity = await client.get_entity(chat.telegram_id)
        return await _import_telegram_messages(client, entity, chat, count, manager)
    finally:
        await client.disconnect()


def import_telegram_history(chat, count):
    account = chat.telegram_account
    return asyncio.run(_import_telegram_history(chat, account, count))


async def _discover_telegram(account, since, per_chat):
    manager = TelegramClientManager()
    client = manager._create_client(StringSession(account.session_string), account.api_id, account.api_hash)
    discovered = imported = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError('Сессия Telegram больше не авторизована.')
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            latest_date = getattr(dialog.message, 'date', None)
            if not dialog.is_user or getattr(entity, 'bot', False) or (since and latest_date and latest_date < since):
                continue
            chat, was_created = await database_sync_to_async(Chat.objects.get_or_create)(
                telegram_id=entity.id, telegram_account=account,
                defaults={'chat_type': Chat.ChatType.PRIVATE, 'title': dialog.title or getattr(entity, 'first_name', None) or str(entity.id), 'username': getattr(entity, 'username', None), 'first_name': getattr(entity, 'first_name', None), 'last_name': getattr(entity, 'last_name', None), 'is_bot': False},
            )
            discovered += int(was_created)
            new, _ = await _import_telegram_messages(client, entity, chat, per_chat, manager)
            imported += new
        return discovered, imported
    finally:
        await client.disconnect()


def discover_telegram(account, since, per_chat=5):
    return asyncio.run(_discover_telegram(account, since, per_chat))


def discover_green(account, since, per_chat=5):
    client = GreenAPIClient(account)
    chats = client.get_chats(count=1000)
    discovered = imported = 0
    media_ids = []
    for item in chats if isinstance(chats, list) else []:
        external_id = str(item.get('id') or '')
        kind = str(item.get('type') or '').lower()
        is_private = kind == 'user' or (account.account_type == TelegramAccount.AccountType.WHATSAPP and external_id.endswith(('@c.us', '@lid')))
        if not external_id or not is_private:
            continue
        preview = client.get_chat_history(external_id, count=per_chat)
        if not preview:
            continue
        newest = max(_green_datetime(message.get('timestamp')) for message in preview)
        if since and newest < since:
            continue
        chat, was_created = Chat.objects.get_or_create(
            telegram_id=(int(external_id.split('@', 1)[0]) if external_id.split('@', 1)[0].isdigit() else int.from_bytes(__import__('hashlib').sha256(external_id.encode()).digest()[:7], 'big')),
            telegram_account=account,
            defaults={
                'chat_type': Chat.ChatType.PRIVATE,
                'title': item.get('name') or external_id,
                'metadata': {'provider': account.account_type, 'external_chat_id': external_id},
            },
        )
        discovered += int(was_created)
        # Reuse the preview: fetching the same history twice doubles provider
        # traffic and noticeably slows first import on a small VPS.
        new, _, stickers = _ingest_green_items(chat, preview)
        imported += new
        media_ids.extend(stickers)
    return discovered, imported, media_ids
