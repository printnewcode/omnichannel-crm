"""Adapters for webhook-based messaging providers."""

from .whatsapp_client import GreenAPIClient


def _send_green_api_message(account, chat, text, media_path=None, reply_to_message=None):
    external_chat_id = (chat.metadata or {}).get('external_chat_id') or chat.telegram_id
    return GreenAPIClient(account).send(
        external_chat_id,
        text=text,
        media_path=media_path,
        reply_to_id=(
            reply_to_message.external_message_id
            if reply_to_message and reply_to_message.external_message_id else None
        ),
    )


def send_whatsapp_message(account, chat, text, media_path=None, reply_to_message=None):
    return _send_green_api_message(account, chat, text, media_path, reply_to_message)


def send_max_message(account, chat, text, media_path=None, reply_to_message=None):
    return _send_green_api_message(account, chat, text, media_path, reply_to_message)