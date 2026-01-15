"""
REST API Views для CRM системы
"""
import asyncio
import logging
from typing import Optional
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import (
    TelegramAccount, Chat, Message, Operator, ChatAssignment
)
from .serializers import (
    TelegramAccountSerializer, ChatSerializer, MessageSerializer,
    OperatorSerializer, ChatAssignmentSerializer, SendMessageSerializer
)
from .services.telegram_client_manager import TelegramClientManager
from .services.message_router import MessageRouter
from .services.health_monitor import HealthMonitor
from .tasks import process_incoming_message

logger = logging.getLogger(__name__)


class TelegramAccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления Telegram аккаунтами
    """
    queryset = TelegramAccount.objects.all()
    serializer_class = TelegramAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Запустить клиент для аккаунта"""
        account = self.get_object()
        
        if account.account_type == TelegramAccount.AccountType.PERSONAL:
            # Запуск Hydrogram клиента
            manager = TelegramClientManager()
            try:
                success = manager.start_client_sync(account)
                
                if success:
                    return Response({'status': 'started'}, status=status.HTTP_200_OK)
                else:
                    return Response(
                        {'error': account.last_error or 'Failed to start client'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                logger.exception(f"Error starting client: {e}")
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            return Response(
                {'error': 'Only personal accounts can be started'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        """Остановить клиент для аккаунта"""
        account = self.get_object()
        
        if account.account_type == TelegramAccount.AccountType.PERSONAL:
            manager = TelegramClientManager()
            try:
                success = manager.stop_client_sync(account.id)
                
                if success:
                    return Response({'status': 'stopped'}, status=status.HTTP_200_OK)
                else:
                    return Response(
                        {'error': 'Failed to stop client'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                logger.exception(f"Error stopping client: {e}")
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            return Response(
                {'error': 'Only personal accounts can be stopped'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        """Перезапустить клиент для аккаунта"""
        account = self.get_object()
        
        if account.account_type == TelegramAccount.AccountType.PERSONAL:
            manager = TelegramClientManager()
            try:
                success = manager.restart_client_sync(account.id)
                
                if success:
                    return Response({'status': 'restarted'}, status=status.HTTP_200_OK)
                else:
                    return Response(
                        {'error': 'Failed to restart client'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                logger.exception(f"Error restarting client: {e}")
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            return Response(
                {'error': 'Only personal accounts can be restarted'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['post'])
    def authenticate(self, request):
        """
        Начало процесса авторизации Hydrogram аккаунта
        Пользователь отправляет телефон, получает OTP код
        """
        phone_number = request.data.get('phone_number')
        api_id = request.data.get('api_id')
        api_hash = request.data.get('api_hash')
        name = request.data.get('name', f"Account {phone_number}")

        if not all([phone_number, api_id, api_hash]):
            return Response(
                {'error': 'phone_number, api_id, api_hash are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Создание аккаунта для авторизации
        account, created = TelegramAccount.objects.get_or_create(
            phone_number=phone_number,
            defaults={
                'name': name,
                'account_type': TelegramAccount.AccountType.PERSONAL,
                'status': TelegramAccount.AccountStatus.INACTIVE,
                'api_id': api_id,
                'api_hash': api_hash
            }
        )

        if not created and account.status == TelegramAccount.AccountStatus.ACTIVE and account.session_string:
            return Response({
                'account_id': account.id,
                'status': 'already_authenticated',
                'message': 'Account is already authenticated'
            }, status=status.HTTP_200_OK)

        # Запуск процесса авторизации
        manager = TelegramClientManager()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(manager.authenticate_account(account))
            loop.close()

            if result['success']:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(
                    {'error': result['error']},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            logger.exception(f"Error in authentication: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def verify_otp(self, request, pk=None):
        """
        Завершение авторизации с OTP кодом
        """
        account = self.get_object()
        otp_code = request.data.get('otp_code')
        password = request.data.get('password')  # Для 2FA

        if not otp_code:
            return Response(
                {'error': 'otp_code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Верификация OTP
        manager = TelegramClientManager()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(manager.verify_otp(account, otp_code, password))
            loop.close()

            if result['success']:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(
                    {'error': result['error']},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            logger.exception(f"Error in OTP verification: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def resend_code(self, request, pk=None):
        """
        Отправить OTP код повторно другим методом верификации
        Полезно когда SMS не пришел (особенно в России)
        """
        account = self.get_object()

        if account.status != TelegramAccount.AccountStatus.AUTHENTICATING:
            return Response(
                {'error': 'Account is not in authentication state'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Resend code with different method
        manager = TelegramClientManager()
        try:
            result = manager.resend_code_sync(account)

            if result['success']:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(
                    {'error': result['error']},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            logger.exception(f"Error resending code: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class HealthCheckView(APIView):
    """
    Проверка здоровья системы
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        """Простая проверка здоровья"""
        return Response({
            'status': 'healthy',
            'timestamp': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)


class SystemStatusView(APIView):
    """
    Детальный статус системы для администраторов
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        """Получение детального статуса системы"""
        try:
            monitor = HealthMonitor()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            status_data = loop.run_until_complete(monitor.get_system_status())
            loop.close()

            return Response(status_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Error getting system status: {e}")
            return Response(
                {
                    'status': 'error',
                    'error': str(e),
                    'timestamp': timezone.now().isoformat()
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SystemControlView(APIView):
    """
    Управление системой для администраторов
    """
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=['post'])
    def restart_clients(self, request):
        """Принудительный перезапуск всех клиентов"""
        try:
            monitor = HealthMonitor()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(monitor.force_restart_all_clients())
            loop.close()

            return Response({
                'status': 'restarted',
                'message': 'All clients restarted successfully'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Error restarting clients: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ChatViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet для просмотра чатов (только чтение)
    """
    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Получить только чаты назначенные оператору"""
        assigned_only = self.request.query_params.get('assigned_only') in ['1', 'true', 'yes']
        if (self.request.user.is_staff or self.request.user.is_superuser) and not assigned_only:
            # Администраторы видят все чаты (если не запрошен фильтр assigned_only)
            return Chat.objects.select_related('telegram_account').order_by('-last_message_at')

        try:
            operator = Operator.objects.get(user=self.request.user, is_active=True)
            assignments = ChatAssignment.objects.filter(
                operator=operator,
                is_active=True
            ).select_related('chat', 'chat__telegram_account')

            return Chat.objects.filter(
                assignment__in=assignments
            ).select_related('telegram_account').order_by('-last_message_at')
        except Operator.DoesNotExist:
            return Chat.objects.none()
    
    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Назначить чат оператору"""
        chat = self.get_object()
        
        try:
            operator = Operator.objects.get(user=request.user, is_active=True)
        except Operator.DoesNotExist:
            return Response(
                {'error': 'User is not an operator'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Проверка лимита чатов
        current_count = ChatAssignment.objects.filter(
            operator=operator,
            is_active=True
        ).count()
        
        if current_count >= operator.max_chats:
            return Response(
                {'error': f'Maximum chats limit reached ({operator.max_chats})'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Создание назначения
        assignment, created = ChatAssignment.objects.get_or_create(
            chat=chat,
            defaults={
                'operator': operator,
                'is_active': True
            }
        )
        
        if not created:
            assignment.is_active = True
            assignment.unassigned_at = None
            assignment.save()
        
        operator.current_chats = ChatAssignment.objects.filter(
            operator=operator,
            is_active=True
        ).count()
        operator.save(update_fields=['current_chats'])
        
        return Response({
            'status': 'assigned',
            'assignment_id': assignment.id
        }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def unassign(self, request, pk=None):
        """Снять назначение чата с оператора"""
        chat = self.get_object()
        
        try:
            operator = Operator.objects.get(user=request.user, is_active=True)
        except Operator.DoesNotExist:
            return Response(
                {'error': 'User is not an operator'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            assignment = ChatAssignment.objects.get(
                chat=chat,
                operator=operator,
                is_active=True
            )
            assignment.is_active = False
            assignment.unassigned_at = timezone.now()
            assignment.save()
            
            operator.current_chats = ChatAssignment.objects.filter(
                operator=operator,
                is_active=True
            ).count()
            operator.save(update_fields=['current_chats'])
            
            return Response({'status': 'unassigned'}, status=status.HTTP_200_OK)
        except ChatAssignment.DoesNotExist:
            return Response(
                {'error': 'Chat is not assigned to this operator'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """Отправить новое сообщение в чат"""
        chat = self.get_object()

        serializer = SendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        text = serializer.validated_data['text']
        media_path = serializer.validated_data.get('media_path')

        # Проверка, что чат назначен оператору
        try:
            operator = Operator.objects.get(user=request.user, is_active=True)
            ChatAssignment.objects.get(
                chat=chat,
                operator=operator,
                is_active=True
            )
        except (Operator.DoesNotExist, ChatAssignment.DoesNotExist):
            return Response(
                {'error': 'Chat is not assigned to this operator'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Отправка сообщения через MessageRouter
        router = MessageRouter()
        try:
            telegram_message_id = router.send_message(chat, text, media_path)

            if telegram_message_id:
                # Создание записи об отправленном сообщении
                outgoing_message = router.create_outgoing_message(
                    chat=chat,
                    text=text,
                    telegram_message_id=telegram_message_id,
                    message_type='text' if not media_path else 'photo',
                    media_file_path=media_path
                )

                # Отправка обновления через WebSocket
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync

                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"operator_{operator.user.id}",
                    {
                        'type': 'new_message',
                        'message': MessageSerializer(outgoing_message).data
                    }
                )

                return Response({
                    'status': 'sent',
                    'message_id': outgoing_message.id,
                    'telegram_message_id': telegram_message_id
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(
                    {'error': 'Failed to send message'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        except Exception as e:
            logger.exception(f"Error sending message: {e}")
            return Response(
                {'error': 'Internal server error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet для просмотра и отправки сообщений
    """
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Получить сообщения из чатов назначенных оператору"""
        if self.request.user.is_staff or self.request.user.is_superuser:
            # Администраторы видят все сообщения
            return Message.objects.select_related('chat', 'chat__telegram_account', 'reply_to_message').order_by('-telegram_date')

        try:
            operator = Operator.objects.get(user=self.request.user, is_active=True)
            assigned_chat_ids = ChatAssignment.objects.filter(
                operator=operator,
                is_active=True
            ).values_list('chat_id', flat=True)

            return Message.objects.filter(
                chat_id__in=assigned_chat_ids
            ).select_related('chat', 'chat__telegram_account', 'reply_to_message').order_by('-telegram_date')
        except Operator.DoesNotExist:
            return Message.objects.none()
    
    def get_queryset_by_chat(self, chat_id):
        """Получить сообщения конкретного чата"""
        # Проверка, что чат назначен оператору
        try:
            operator = Operator.objects.get(user=self.request.user, is_active=True)
            ChatAssignment.objects.get(
                chat_id=chat_id,
                operator=operator,
                is_active=True
            )
            return Message.objects.filter(chat_id=chat_id).select_related(
                'chat', 'chat__telegram_account', 'reply_to_message'
            ).order_by('-telegram_date')
        except (Operator.DoesNotExist, ChatAssignment.DoesNotExist):
            return Message.objects.none()
    
    @action(detail=False, methods=['get'])
    def by_chat(self, request):
        """Получить сообщения по chat_id"""
        chat_id = request.query_params.get('chat_id')
        if not chat_id:
            return Response(
                {'error': 'chat_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        messages = self.get_queryset_by_chat(chat_id)
        page = self.paginate_queryset(messages)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(messages, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        """Отправить ответ на сообщение"""
        message = self.get_object()
        
        serializer = SendMessageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        text = serializer.validated_data['text']
        media_path = serializer.validated_data.get('media_path')
        
        # Проверка, что чат назначен оператору
        try:
            operator = Operator.objects.get(user=request.user, is_active=True)
            ChatAssignment.objects.get(
                chat=message.chat,
                operator=operator,
                is_active=True
            )
        except (Operator.DoesNotExist, ChatAssignment.DoesNotExist):
            return Response(
                {'error': 'Chat is not assigned to this operator'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Отправка ответа через MessageRouter
        router = MessageRouter()
        try:
            telegram_message_id = router.send_reply(message, text, media_path)
            
            if telegram_message_id:
                # Создание записи об отправленном сообщении
                outgoing_message = router.create_outgoing_message(
                    chat=message.chat,
                    text=text,
                    telegram_message_id=telegram_message_id,
                    reply_to_message=message,
                    message_type='text' if not media_path else 'photo',
                    media_file_path=media_path
                )
                
                # Отправка обновления через WebSocket
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"operator_{operator.user.id}",
                    {
                        'type': 'new_message',
                        'message': {
                            'id': outgoing_message.id,
                            'telegram_id': outgoing_message.telegram_id,
                            'chat_id': outgoing_message.chat.id,
                            'text': outgoing_message.text,
                            'message_type': outgoing_message.message_type,
                            'status': outgoing_message.status,
                            'is_outgoing': True,
                            'telegram_date': outgoing_message.telegram_date.isoformat(),
                        }
                    }
                )
                
                return Response({
                    'status': 'sent',
                    'message_id': outgoing_message.id,
                    'telegram_message_id': telegram_message_id
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(
                    {'error': 'Failed to send message'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        except Exception as e:
            logger.exception(f"Error sending reply: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BotWebhookView(APIView):
    """
    Webhook endpoint для существующего бота (pyTelegramBotAPI)
    Получает обновления от бота и сохраняет в CRM
    """
    permission_classes = [permissions.AllowAny]  # Webhook может быть без авторизации

    def post(self, request, token=None):
        """
        Обработка webhook от телеграм бота

        Формат: стандартный Update объект от Telegram Bot API
        """
        try:
            update_data = request.data

            # Получение токена бота из URL или заголовка
            bot_token = token or request.headers.get('X-Bot-Token') or request.query_params.get('token')

            if not bot_token:
                logger.warning("Webhook received without bot token")
                return Response(
                    {'error': 'Bot token is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Валидация токена (должен быть валидным бот токеном)
            if not self._validate_bot_token(bot_token):
                logger.warning(f"Invalid bot token format received")
                return Response(
                    {'error': 'Invalid bot token'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Поиск аккаунта бота
            try:
                account = TelegramAccount.objects.get(
                    bot_token=bot_token,
                    account_type=TelegramAccount.AccountType.BOT
                )
            except TelegramAccount.DoesNotExist:
                logger.warning(f"Bot account not found for token: {bot_token[:10]}...")
                return Response(
                    {'error': 'Bot account not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Проверка, что аккаунт активен
            if account.status != TelegramAccount.AccountStatus.ACTIVE:
                logger.warning(f"Bot account {account.id} is not active")
                return Response(
                    {'error': 'Bot account is not active'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Обработка update (стандартный формат Telegram Bot API)
            update_id = update_data.get('update_id')
            message_data = update_data.get('message')
            edited_message_data = update_data.get('edited_message')

            if not message_data and not edited_message_data:
                # Другие типы обновлений (callback_query, inline_query, etc.)
                # Можно обработать при необходимости
                logger.info(f"Received non-message update: {list(update_data.keys())}")
                return Response({'status': 'processed'}, status=status.HTTP_200_OK)

            # Определяем, какое сообщение обрабатывать
            message_data = message_data or edited_message_data
            is_edited = edited_message_data is not None

            # Получение или создание чата
            chat_data = message_data.get('chat', {})
            chat_telegram_id = chat_data.get('id')

            if not chat_telegram_id:
                logger.error("Chat ID not found in message data")
                return Response(
                    {'error': 'Chat ID not found'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Создание или обновление чата
            chat, created = Chat.objects.get_or_create(
                telegram_id=chat_telegram_id,
                telegram_account=account,
                defaults={
                    'chat_type': chat_data.get('type', 'private'),
                    'title': chat_data.get('title'),
                    'username': chat_data.get('username'),
                    'first_name': chat_data.get('first_name'),
                    'last_name': chat_data.get('last_name'),
                    'metadata': {}
                }
            )

            # Обновление информации о чате если он уже существует
            if not created:
                updated = False
                if chat_data.get('title') and chat.title != chat_data.get('title'):
                    chat.title = chat_data.get('title')
                    updated = True
                if chat_data.get('username') and chat.username != chat_data.get('username'):
                    chat.username = chat_data.get('username')
                    updated = True
                if updated:
                    chat.save()

            # Обработка сообщения через Celery задачу
            from_user = message_data.get('from', {})
            message_id = message_data.get('message_id')
            message_date = message_data.get('date')  # Unix timestamp

            # Конвертация даты
            from datetime import datetime
            message_date_obj = datetime.fromtimestamp(message_date) if message_date else timezone.now()
            if message_date_obj.tzinfo is None:
                message_date_obj = timezone.make_aware(message_date_obj)

            # Определение типа сообщения и медиа
            message_type, media_file_id, media_caption = self._parse_message_type(message_data)

            # Проверка на исходящее сообщение (от бота)
            from_user_id = from_user.get('id')
            is_outgoing = from_user_id == account.telegram_user_id

            # Запуск задачи обработки сообщения
            process_incoming_message.delay(
                account_id=account.id,
                chat_id=chat.id,
                telegram_message_id=message_id,
                telegram_date=message_date_obj.isoformat(),
                text=message_data.get('text') or media_caption,
                from_user_id=from_user_id,
                from_user_name=from_user.get('first_name'),
                from_user_username=from_user.get('username'),
                is_outgoing=is_outgoing,
                reply_to_message_id=message_data.get('reply_to_message', {}).get('message_id'),
                message_type=message_type,
                media_file_id=media_file_id,
                media_caption=media_caption
            )

            logger.info(f"Webhook processed message {message_id} for bot {account.id}")
            return Response({'status': 'processed'}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Error processing webhook: {e}")
            return Response(
                {'error': 'Internal server error'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _validate_bot_token(self, token: str) -> bool:
        """Простая валидация формата бот токена"""
        import re
        # Bot token format: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
        pattern = r'^\d{8,10}:[A-Za-z0-9_-]{35}$'
        return bool(re.match(pattern, token))

    def _parse_message_type(self, message_data: dict) -> tuple:
        """Определение типа сообщения и извлечение медиа данных"""
        if message_data.get('photo'):
            message_type = 'photo'
            # Берем самое большое фото (последнее в массиве)
            photos = message_data.get('photo', [])
            media_file_id = photos[-1].get('file_id') if photos else None
            media_caption = message_data.get('caption')
        elif message_data.get('video'):
            message_type = 'video'
            media_file_id = message_data.get('video', {}).get('file_id')
            media_caption = message_data.get('caption')
        elif message_data.get('voice'):
            message_type = 'voice'
            media_file_id = message_data.get('voice', {}).get('file_id')
            media_caption = None
        elif message_data.get('audio'):
            message_type = 'audio'
            media_file_id = message_data.get('audio', {}).get('file_id')
            media_caption = message_data.get('caption')
        elif message_data.get('document'):
            message_type = 'document'
            media_file_id = message_data.get('document', {}).get('file_id')
            media_caption = message_data.get('caption')
        elif message_data.get('sticker'):
            message_type = 'sticker'
            media_file_id = message_data.get('sticker', {}).get('file_id')
            media_caption = None
        elif message_data.get('location'):
            message_type = 'location'
            media_file_id = None
            media_caption = None
        elif message_data.get('contact'):
            message_type = 'contact'
            media_file_id = None
            media_caption = None
        elif message_data.get('text'):
            message_type = 'text'
            media_file_id = None
            media_caption = None
        else:
            message_type = 'other'
            media_file_id = None
            media_caption = None

        return message_type, media_file_id, media_caption


class FileUploadView(APIView):
    """
    Загрузка файлов для отправки в сообщениях
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Загрузка файла"""
        try:
            uploaded_file = request.FILES.get('file')
            if not uploaded_file:
                return Response(
                    {'error': 'No file provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Валидация размера файла (макс 50MB)
            if uploaded_file.size > 50 * 1024 * 1024:
                return Response(
                    {'error': 'File too large. Maximum size is 50MB'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Валидация типа файла
            allowed_types = [
                'image/jpeg', 'image/png', 'image/gif', 'image/webp',
                'video/mp4', 'video/avi', 'video/mov', 'video/mkv',
                'audio/mp3', 'audio/wav', 'audio/ogg',
                'application/pdf', 'application/zip', 'application/x-rar-compressed'
            ]

            if uploaded_file.content_type not in allowed_types:
                # Дополнительная проверка по расширению для файлов без правильного content-type
                allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.avi', '.mov', '.mkv', '.mp3', '.wav', '.ogg', '.pdf', '.zip', '.rar']
                file_ext = '.' + uploaded_file.name.split('.')[-1].lower() if '.' in uploaded_file.name else ''

                if file_ext not in allowed_extensions:
                    return Response(
                        {'error': f'File type not allowed. Allowed types: {", ".join(allowed_types)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Создание уникального имени файла
            import uuid
            import os
            from datetime import datetime

            today = datetime.now().strftime('%Y/%m/%d')
            file_extension = os.path.splitext(uploaded_file.name)[1]
            unique_filename = f"{uuid.uuid4()}{file_extension}"

            # Путь для сохранения
            file_path = f"uploads/{today}/{unique_filename}"

            # Сохранение файла
            saved_path = default_storage.save(file_path, ContentFile(uploaded_file.read()))

            # Получение полного пути для доступа
            full_path = os.path.join(settings.MEDIA_ROOT, saved_path)

            return Response({
                'file_path': saved_path,
                'file_url': request.build_absolute_uri(settings.MEDIA_URL + saved_path),
                'file_name': uploaded_file.name,
                'file_size': uploaded_file.size,
                'content_type': uploaded_file.content_type
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Error uploading file: {e}")
            return Response(
                {'error': 'File upload failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
