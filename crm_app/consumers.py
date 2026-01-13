"""
Django Channels Consumers для real-time обновлений через WebSockets
"""
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from .models import Message, Chat, Operator, ChatAssignment

logger = logging.getLogger(__name__)


class MessageConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для real-time обновлений сообщений
    Каждый оператор подписывается на обновления своих чатов
    """
    
    async def connect(self):
        """Подключение к WebSocket"""
        self.user = self.scope["user"]
        self.room_group_name = f"operator_{self.user.id}"
        
        # Проверка авторизации
        if not self.user.is_authenticated:
            await self.close()
            return
        
        # Проверка, что пользователь является оператором
        try:
            operator = await database_sync_to_async(Operator.objects.get)(
                user=self.user,
                is_active=True
            )
            self.operator = operator
        except Operator.DoesNotExist:
            logger.warning(f"User {self.user.id} is not an operator")
            await self.close()
            return
        
        # Присоединение к группе оператора
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.info(f"Operator {self.user.id} connected to WebSocket")
        
        # Отправка начальных данных (список чатов)
        await self.send_initial_chats()
    
    async def disconnect(self, close_code):
        """Отключение от WebSocket"""
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
        logger.info(f"Operator {self.user.id} disconnected from WebSocket")
    
    async def receive(self, text_data):
        """Получение сообщения от клиента"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')

            # Валидация входных данных
            if not isinstance(data, dict):
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Invalid message format'
                }))
                return

            if message_type == 'get_chat_messages':
                # Запрос сообщений чата
                chat_id = data.get('chat_id')
                if not chat_id:
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'chat_id is required for get_chat_messages'
                    }))
                    return

                try:
                    chat_id = int(chat_id)
                    await self.send_chat_messages(chat_id)
                except (ValueError, TypeError):
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Invalid chat_id format'
                    }))

            elif message_type == 'mark_as_read':
                # Отметить чат как прочитанный
                chat_id = data.get('chat_id')
                if not chat_id:
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'chat_id is required for mark_as_read'
                    }))
                    return

                try:
                    chat_id = int(chat_id)
                    await self.mark_chat_as_read(chat_id)
                except (ValueError, TypeError):
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Invalid chat_id format'
                    }))

            elif message_type == 'ping':
                # Проверка соединения
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                    'timestamp': data.get('timestamp')
                }))

            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'Unknown message type: {message_type}'
                }))

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received from user {self.user.id}: {text_data[:100]}...")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            logger.exception(f"Error processing WebSocket message from user {self.user.id}: {e}")
            try:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Internal server error'
                }))
            except:
                pass  # Соединение может быть уже закрыто
    
    async def send_initial_chats(self):
        """Отправка списка чатов оператору"""
        chats = await database_sync_to_async(self._get_operator_chats)()

        # Отправка чатов порциями если их много
        if len(chats) > 50:
            # Отправка первой порции
            await self.send(text_data=json.dumps({
                'type': 'initial_chats_start',
                'total': len(chats),
                'chats': chats[:50]
            }))

            # Отправка оставшихся порциями
            for i in range(50, len(chats), 50):
                await self.send(text_data=json.dumps({
                    'type': 'initial_chats_chunk',
                    'chats': chats[i:i+50]
                }))

            # Завершение
            await self.send(text_data=json.dumps({
                'type': 'initial_chats_end'
            }))
        else:
            await self.send(text_data=json.dumps({
                'type': 'initial_chats',
                'chats': chats
            }))
    
    def _get_operator_chats(self):
        """Получить чаты оператора"""
        assignments = ChatAssignment.objects.filter(
            operator=self.operator,
            is_active=True
        ).select_related('chat', 'chat__telegram_account').order_by(
            '-chat__last_message_at'
        )
        
        chats = []
        for assignment in assignments:
            chat = assignment.chat
            chats.append({
                'id': chat.id,
                'telegram_id': chat.telegram_id,
                'title': chat.title or chat.first_name or chat.username or f"Chat {chat.telegram_id}",
                'chat_type': chat.chat_type,
                'unread_count': chat.unread_count,
                'last_message_at': chat.last_message_at.isoformat() if chat.last_message_at else None,
                'telegram_account': {
                    'id': chat.telegram_account.id,
                    'name': chat.telegram_account.name,
                    'account_type': chat.telegram_account.account_type,
                }
            })
        
        return chats
    
    async def send_chat_messages(self, chat_id: int):
        """Отправка сообщений чата"""
        messages = await database_sync_to_async(self._get_chat_messages)(chat_id)
        
        await self.send(text_data=json.dumps({
            'type': 'chat_messages',
            'chat_id': chat_id,
            'messages': messages
        }))
    
    def _get_chat_messages(self, chat_id: int):
        """Получить сообщения чата"""
        # Проверка, что чат назначен оператору
        try:
            assignment = ChatAssignment.objects.get(
                chat_id=chat_id,
                operator=self.operator,
                is_active=True
            )
        except ChatAssignment.DoesNotExist:
            return []
        
        messages = Message.objects.filter(
            chat_id=chat_id
        ).select_related('reply_to_message').order_by('telegram_date')[:100]
        
        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'telegram_id': msg.telegram_id,
                'text': msg.text,
                'message_type': msg.message_type,
                'status': msg.status,
                'is_outgoing': msg.is_outgoing,
                'from_user_name': msg.from_user_name,
                'from_user_username': msg.from_user_username,
                'telegram_date': msg.telegram_date.isoformat(),
                'media_file_path': msg.media_file_path,
                'media_caption': msg.media_caption,
                'reply_to_message_id': msg.reply_to_message_id,
            })
        
        return result
    
    async def mark_chat_as_read(self, chat_id: int):
        """Отметить чат как прочитанный"""
        await database_sync_to_async(self._mark_chat_as_read)(chat_id)
        
        await self.send(text_data=json.dumps({
            'type': 'chat_marked_as_read',
            'chat_id': chat_id
        }))
    
    def _mark_chat_as_read(self, chat_id: int):
        """Отметить чат как прочитанный (sync)"""
        try:
            chat = Chat.objects.get(id=chat_id)
            chat.unread_count = 0
            chat.save(update_fields=['unread_count'])
        except Chat.DoesNotExist:
            pass
    
    # Handler для групповых сообщений (от Celery/системы)
    async def new_message(self, event):
        """Обработка нового сообщения от системы"""
        message_data = event['message']
        
        # Проверка, что чат назначен оператору
        chat_id = message_data.get('chat_id')
        is_assigned = await database_sync_to_async(self._is_chat_assigned)(chat_id)
        
        if is_assigned:
            await self.send(text_data=json.dumps({
                'type': 'new_message',
                'message': message_data
            }))
    
    def _is_chat_assigned(self, chat_id: int) -> bool:
        """Проверка, назначен ли чат оператору"""
        return ChatAssignment.objects.filter(
            chat_id=chat_id,
            operator=self.operator,
            is_active=True
        ).exists()
    
    async def chat_updated(self, event):
        """Обработка обновления чата"""
        chat_data = event['chat']
        chat_id = chat_data.get('id')
        
        is_assigned = await database_sync_to_async(self._is_chat_assigned)(chat_id)
        
        if is_assigned:
            await self.send(text_data=json.dumps({
                'type': 'chat_updated',
                'chat': chat_data
            }))
