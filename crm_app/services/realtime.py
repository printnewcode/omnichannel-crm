"""Cross-process realtime notifications for the shared CRM queue."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import close_old_connections, connection

from ..models import Chat, Message
from ..serializers import ChatSerializer, MessageSerializer

logger = logging.getLogger(__name__)
CRM_OPERATORS_GROUP = 'crm_operators'


def publish_message(message_id: int) -> None:
    """Publish a persisted message and its updated chat to every online operator."""
    manage_connection = not connection.in_atomic_block
    if manage_connection:
        close_old_connections()
    try:
        message = Message.objects.select_related('chat', 'chat__telegram_account').get(pk=message_id)
        if message.chat.chat_type != Chat.ChatType.PRIVATE or message.chat.is_bot:
            return
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(CRM_OPERATORS_GROUP, {
            'type': 'new_message',
            'message': MessageSerializer(message).data,
        })
        async_to_sync(layer.group_send)(CRM_OPERATORS_GROUP, {
            'type': 'chat_updated',
            'chat': ChatSerializer(message.chat).data,
        })
    except Message.DoesNotExist:
        logger.warning('Cannot publish missing message %s', message_id)
    except Exception:
        logger.exception('Could not publish realtime event for message %s', message_id)
    finally:
        if manage_connection:
            close_old_connections()


def publish_delivery(delivery) -> None:
    """Publish outbound delivery state to every online operator."""
    try:
        if delivery.chat.chat_type != Chat.ChatType.PRIVATE or delivery.chat.is_bot:
            return
        async_to_sync(get_channel_layer().group_send)(CRM_OPERATORS_GROUP, {
            'type': 'delivery_updated',
            'delivery': {
                'id': delivery.id,
                'chat_id': delivery.chat_id,
                'status': delivery.status,
                'attempts': delivery.attempts,
                'last_error': delivery.last_error,
            },
        })
    except Exception:
        logger.exception('Could not publish outbound delivery %s', delivery.pk)