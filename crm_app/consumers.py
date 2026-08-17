"""Django Channels consumer for the shared CRM conversation queue."""

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Chat, Message
from .serializers import MessageSerializer
from .services.realtime import CRM_OPERATORS_GROUP

logger = logging.getLogger(__name__)


class MessageConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            await self.close()
            return
        self.room_group_name = CRM_OPERATORS_GROUP
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        # The HTTP endpoint is the single source of truth for the complete chat
        # list. Sending it again here used to perform hundreds of queries on
        # every reconnect and could starve the single-core VPS.
        await self.send(text_data=json.dumps({'type': 'ready'}))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (TypeError, json.JSONDecodeError):
            await self.send(text_data=json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
            return
        message_type = data.get('type')
        if message_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong', 'timestamp': data.get('timestamp')}))
        elif message_type == 'get_chat_messages':
            messages = await database_sync_to_async(self._chat_messages)(data.get('chat_id'))
            await self.send(text_data=json.dumps({'type': 'chat_messages', 'chat_id': data.get('chat_id'), 'messages': messages}))
        elif message_type == 'mark_as_read':
            await database_sync_to_async(self._mark_read)(data.get('chat_id'))
            await self.send(text_data=json.dumps({'type': 'chat_marked_as_read', 'chat_id': data.get('chat_id')}))

    @staticmethod
    def _chat_messages(chat_id):
        queryset = Message.objects.filter(
            chat_id=chat_id,
            chat__chat_type=Chat.ChatType.PRIVATE,
            chat__is_bot=False,
        ).select_related('reply_to_message').order_by('telegram_date')[:100]
        return MessageSerializer(queryset, many=True).data

    @staticmethod
    def _mark_read(chat_id):
        Chat.objects.filter(pk=chat_id, chat_type=Chat.ChatType.PRIVATE, is_bot=False).update(unread_count=0)

    async def new_message(self, event):
        await self.send(text_data=json.dumps({'type': 'new_message', 'message': event['message']}))

    async def delivery_updated(self, event):
        await self.send(text_data=json.dumps({'type': 'delivery_updated', 'delivery': event['delivery']}))

    async def chat_updated(self, event):
        await self.send(text_data=json.dumps({'type': 'chat_updated', 'chat': event['chat']}))
