"""
REST API Views для CRM системы
"""
import asyncio
import errno
import logging
import os
import uuid
from pathlib import Path
from typing import Optional
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, Substr
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.core.exceptions import SuspiciousFileOperation
from django.utils.text import get_valid_filename
from django.shortcuts import redirect
from .models import (
    TelegramAccount, Chat, Message, HistoryImportJob
)
from .serializers import (
    TelegramAccountSerializer, ChatSerializer, MessageSerializer,
    SendMessageSerializer, HistoryImportJobSerializer
)
from .services.telegram_client_manager import TelegramClientManager
from .services.message_router import MessageRouter
from .services.health_monitor import HealthMonitor
from .tasks import process_incoming_message

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 10


class MessagePagination(PageNumberPagination):
    """Allow an explicit, bounded history window for user-requested imports."""

    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 10000


class ChatPagination(PageNumberPagination):
    """Serve the conversation list in moderate chunks for the small VPS."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


def _enqueue_message_batch(*, chat, text, media_paths, requested_by, reply_to_message=None):
    """Create one provider-neutral batch; providers consume each attachment reliably."""
    from .services.outbound_delivery import enqueue_delivery

    paths = list(media_paths or [])
    with transaction.atomic():
        if not paths:
            return [enqueue_delivery(
                chat=chat,
                text=text,
                reply_to_message=reply_to_message,
                requested_by=requested_by,
            )]
        return [
            enqueue_delivery(
                chat=chat,
                text=text if index == 0 else '',
                media_path=media_path,
                reply_to_message=reply_to_message if index == 0 else None,
                requested_by=requested_by,
            )
            for index, media_path in enumerate(paths)
        ]


def _delivery_response(deliveries):
    return {
        'status': 'pending',
        'delivery_id': deliveries[0].id,
        'delivery_ids': [delivery.id for delivery in deliveries],
        'deliveries': [
            {
                'id': delivery.id,
                'idempotency_key': str(delivery.idempotency_key),
                'media_path': delivery.media_path,
            }
            for delivery in deliveries
        ],
        'idempotency_key': str(deliveries[0].idempotency_key),
    }


class TelegramAccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet для управления Telegram аккаунтами
    """
    queryset = TelegramAccount.objects.all()
    serializer_class = TelegramAccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'])
    def import_chats(self, request):
        """Queue discovery of private, non-bot chats for active accounts."""
        from datetime import datetime, timedelta
        from .tasks import run_history_import

        messenger = request.data.get('messenger', 'telegram')
        account_types = {
            'telegram': [TelegramAccount.AccountType.PERSONAL],
            'whatsapp': [TelegramAccount.AccountType.WHATSAPP],
            'max': [TelegramAccount.AccountType.MAX],
            'all': [
                TelegramAccount.AccountType.PERSONAL,
                TelegramAccount.AccountType.WHATSAPP,
                TelegramAccount.AccountType.MAX,
            ],
        }.get(messenger)
        if not account_types:
            return Response({'error': 'Неизвестный мессенджер.'}, status=status.HTTP_400_BAD_REQUEST)
        since_value = request.data.get('since')
        if since_value:
            try:
                since = datetime.fromisoformat(str(since_value)).date()
                since_iso = timezone.make_aware(datetime.combine(since, datetime.min.time())).isoformat()
            except (TypeError, ValueError):
                return Response({'error': 'Некорректная дата.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            since_iso = (timezone.now() - timedelta(days=60)).isoformat()
        accounts = TelegramAccount.objects.filter(account_type__in=account_types, status=TelegramAccount.AccountStatus.ACTIVE)
        jobs = []
        for account in accounts:
            existing = HistoryImportJob.objects.filter(
                kind=HistoryImportJob.Kind.CHAT_DISCOVERY,
                account=account,
                status__in=[HistoryImportJob.Status.PENDING, HistoryImportJob.Status.RUNNING],
            ).first()
            if existing:
                jobs.append(existing)
                continue
            job = HistoryImportJob.objects.create(
                kind=HistoryImportJob.Kind.CHAT_DISCOVERY,
                account=account,
                requested_by=request.user,
                parameters={'since': since_iso, 'messages_per_chat': 5},
            )
            run_history_import.delay(job.id)
            jobs.append(job)
        if not jobs:
            return Response({'error': 'Нет активных аккаунтов выбранного мессенджера.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'jobs': HistoryImportJobSerializer(jobs, many=True).data}, status=status.HTTP_202_ACCEPTED)
    
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
            result = manager.run_async_sync(manager.authenticate_account(account))

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
            result = manager.run_async_sync(manager.verify_otp(account, otp_code, password))

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
            status_data = TelegramClientManager().run_async_sync(monitor.get_system_status())

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
            TelegramClientManager().run_async_sync(monitor.force_restart_all_clients())

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
    """Общий список диалогов для всех авторизованных операторов."""

    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ChatPagination

    @action(detail=True, methods=['post'])
    def import_history(self, request, pk=None):
        from .tasks import run_history_import

        chat = self.get_object()
        load_all = bool(request.data.get('all'))
        count = request.data.get('count')
        if not load_all:
            try:
                count = int(count)
            except (TypeError, ValueError):
                return Response({'error': 'Укажите количество сообщений.'}, status=status.HTTP_400_BAD_REQUEST)
            if count < 1 or count > 10000:
                return Response({'error': 'Количество должно быть от 1 до 10 000.'}, status=status.HTTP_400_BAD_REQUEST)
        existing = HistoryImportJob.objects.filter(
            kind=HistoryImportJob.Kind.CHAT_HISTORY,
            chat=chat,
            status__in=[HistoryImportJob.Status.PENDING, HistoryImportJob.Status.RUNNING],
        ).first()
        if existing:
            return Response(HistoryImportJobSerializer(existing).data, status=status.HTTP_202_ACCEPTED)
        job = HistoryImportJob.objects.create(
            kind=HistoryImportJob.Kind.CHAT_HISTORY,
            account=chat.telegram_account,
            chat=chat,
            requested_by=request.user,
            parameters={'count': None if load_all else count, 'all': load_all},
        )
        run_history_import.delay(job.id)
        return Response(HistoryImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    def _visible_chats(self):
        """Apply cheap list filters before previews and pagination are built."""
        queryset = Chat.objects.select_related('telegram_account').filter(
            chat_type=Chat.ChatType.PRIVATE,
            is_bot=False,
        ).only(
            'id', 'telegram_id', 'telegram_account_id', 'chat_type', 'title',
            'username', 'first_name', 'last_name', 'message_count',
            'unread_count', 'created_at', 'updated_at', 'last_message_at',
            'is_archived', 'is_bot', 'telegram_account__id',
            'telegram_account__name', 'telegram_account__account_type',
            'telegram_account__status',
        )
        messenger = self.request.query_params.get('messenger', 'all').strip().lower()
        account_types = {
            'telegram': [
                TelegramAccount.AccountType.PERSONAL,
                TelegramAccount.AccountType.BOT,
            ],
            'max': [TelegramAccount.AccountType.MAX],
            'whatsapp': [TelegramAccount.AccountType.WHATSAPP],
        }.get(messenger)
        if account_types is not None:
            queryset = queryset.filter(telegram_account__account_type__in=account_types)

        search_query = (self.request.query_params.get('search') or '').strip()
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query)
                | Q(username__icontains=search_query)
                | Q(first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
                | Q(telegram_account__name__icontains=search_query)
            )
        return queryset

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        totals = self._visible_chats().aggregate(
            active_count=Count('id', filter=Q(is_archived=False)),
            archive_count=Count('id', filter=Q(is_archived=True)),
        )
        response.data.update(totals)
        return response

    def get_queryset(self):
        # Group messages remain persisted, but are intentionally hidden from the
        # operator workspace until group-chat UX is ready.
        # last_message_at is maintained by ingestion/outbox code and indexed.
        # Ordering by correlated subqueries forced MySQL to inspect messages
        # for every chat before returning even the first page.
        latest_message = Message.objects.filter(chat_id=OuterRef('pk')).order_by('-telegram_date').annotate(
            preview=Substr(Coalesce('text', 'media_caption'), 1, 100),
        )
        queryset = self._visible_chats().annotate(
            latest_stored_message_preview=Subquery(latest_message.values('preview')[:1]),
        )
        archived = self.request.query_params.get('archived')
        if archived in {'1', 'true', 'yes'}:
            queryset = queryset.filter(is_archived=True)
        elif archived in {'0', 'false', 'no'}:
            queryset = queryset.filter(is_archived=False)
        return queryset.order_by('-last_message_at', '-updated_at', '-id')

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        chat = self.get_object()
        chat.is_archived = True
        chat.save(update_fields=['is_archived', 'updated_at'])
        return Response({'status': 'archived', 'is_archived': True})

    @action(detail=True, methods=['post'])
    def unarchive(self, request, pk=None):
        chat = self.get_object()
        chat.is_archived = False
        chat.save(update_fields=['is_archived', 'updated_at'])
        return Response({'status': 'active', 'is_archived': False})

    @action(detail=True, methods=['post'])
    def mark_as_read(self, request, pk=None):
        chat = self.get_object()
        chat.unread_count = 0
        chat.save(update_fields=['unread_count'])
        return Response({'status': 'success'})

    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        chat = self.get_object()
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deliveries = _enqueue_message_batch(
            chat=chat,
            text=serializer.validated_data.get('text', ''),
            media_paths=serializer.validated_data.get('media_paths', []),
            requested_by=request.user,
        )
        return Response(_delivery_response(deliveries), status=status.HTTP_202_ACCEPTED)


class HistoryImportJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HistoryImportJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return HistoryImportJob.objects.filter(requested_by=self.request.user)


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """Просмотр сообщений и ответы без ручного назначения диалогов."""

    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = MessagePagination

    def get_queryset(self):
        return Message.objects.filter(
            chat__chat_type=Chat.ChatType.PRIVATE,
            chat__is_bot=False,
        ).annotate(
            api_provider_status=KeyTextTransform('provider_status', 'metadata'),
            api_delivery_id=KeyTextTransform('delivery_id', 'metadata'),
            api_original_filename=KeyTextTransform('original_filename', 'metadata'),
            api_reactions=KeyTransform('reactions', 'metadata'),
        ).select_related(
            'chat', 'chat__telegram_account', 'reply_to_message'
        ).only(
            'id', 'telegram_id', 'external_message_id', 'chat_id',
            'message_type', 'status', 'text', 'is_outgoing',
            'from_user_id', 'from_user_name', 'from_user_username',
            'media_file_id', 'media_file_path', 'media_caption',
            'telegram_date', 'created_at', 'updated_at', 'reply_to_message_id',
            'chat__id', 'chat__title', 'chat__telegram_account_id',
            'chat__telegram_account__id', 'chat__telegram_account__account_type',
            'chat__telegram_account__bot_token', 'chat__telegram_account__bridge_url',
            'reply_to_message__id', 'reply_to_message__text',
            'reply_to_message__media_caption',
        ).order_by('-telegram_date')

    def get_queryset_by_chat(self, chat_id):
        return self.get_queryset().filter(chat_id=chat_id)

    @action(detail=False, methods=['get'])
    def by_chat(self, request):
        chat_id = request.query_params.get('chat_id')
        if not chat_id:
            return Response({'error': 'chat_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        messages = self.get_queryset_by_chat(chat_id)
        search_query = (request.query_params.get('search') or '').strip()
        if search_query:
            # MySQL and SQLite differ in Unicode case-insensitive matching.
            # Python casefold keeps Russian search predictable on both engines.
            folded = search_query.casefold()
            matching_ids = [
                message_id
                for message_id, text, caption in messages.values_list('id', 'text', 'media_caption').iterator(chunk_size=500)
                if folded in (text or '').casefold() or folded in (caption or '').casefold()
            ]
            messages = messages.filter(id__in=matching_ids)
        page = self.paginate_queryset(messages)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(messages, many=True).data)

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        message = self.get_object()
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deliveries = _enqueue_message_batch(
            chat=message.chat,
            text=serializer.validated_data.get('text', ''),
            media_paths=serializer.validated_data.get('media_paths', []),
            reply_to_message=message,
            requested_by=request.user,
        )
        return Response(_delivery_response(deliveries), status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def react(self, request, pk=None):
        from .services.outbound_delivery import enqueue_reaction
        from .services.reactions import normalize_reaction

        message = self.get_object()
        emoji = normalize_reaction(request.data.get('emoji'))
        if not emoji:
            return Response({'error': 'Выберите доступную реакцию.'}, status=status.HTTP_400_BAD_REQUEST)
        account = message.chat.telegram_account
        can_react = (
            account.account_type == TelegramAccount.AccountType.PERSONAL
            or (
                account.account_type == TelegramAccount.AccountType.BOT
                and account.bot_token and not account.bridge_url
            )
        ) and bool(message.telegram_id)
        if not can_react:
            return Response(
                {'error': 'Провайдер этого мессенджера пока не поддерживает отправку реакций через API.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        delivery = enqueue_reaction(message=message, emoji=emoji, requested_by=request.user)
        return Response({
            'status': 'pending',
            'delivery_id': delivery.id,
            'emoji': emoji,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def download_media(self, request, pk=None):
        message = self.get_object()

        def ready(path):
            name = (message.metadata or {}).get('original_filename') or Path(path).name
            return Response({
                'status': 'ready',
                'media_url': request.build_absolute_uri(settings.MEDIA_URL + path),
                'file_name': name,
            })

        if message.media_file_path and default_storage.exists(message.media_file_path):
            return ready(message.media_file_path)
        if not message.message_type or message.message_type == Message.MessageType.TEXT:
            return Response(
                {'error': 'В сообщении нет файла.', 'code': 'media_not_available'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            account = message.chat.telegram_account
            if account.account_type in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
                from .services.provider_media import download_green_api_media
                media_path = download_green_api_media(message)
            else:
                media_path = TelegramClientManager().download_media_by_message_id_sync(message)
            if media_path:
                return ready(media_path)
        except ValueError as exc:
            return Response({'error': str(exc), 'code': 'media_rejected'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as exc:
            logger.exception('Error downloading media for message %s', message.id)
            return Response(
                {'error': f'Не удалось скачать файл у провайдера: {exc}', 'code': 'provider_download_failed'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {'error': 'Файл больше недоступен у провайдера.', 'code': 'media_not_available'},
            status=status.HTTP_404_NOT_FOUND,
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

            # Каналы не входят в операторскую очередь: только личные чаты и группы.
            incoming_chat_type = chat_data.get('type', 'private')
            if incoming_chat_type not in {'private', 'group', 'supergroup'}:
                return Response({'status': 'ignored'}, status=status.HTTP_200_OK)

            # Создание или обновление чата
            chat, created = Chat.objects.get_or_create(
                telegram_id=chat_telegram_id,
                telegram_account=account,
                defaults={
                    'chat_type': incoming_chat_type,
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
    """Accept any file extension and preserve its user-facing filename."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'Выберите файл.', 'code': 'file_missing'}, status=400)
        if uploaded_file.size == 0:
            return Response({'error': 'Файл пустой.', 'code': 'file_empty'}, status=400)
        if uploaded_file.size > MAX_UPLOAD_BYTES:
            return Response({
                'error': 'Файл слишком большой. Максимальный размер — 100 МБ.',
                'code': 'file_too_large',
                'limit_bytes': MAX_UPLOAD_BYTES,
                'actual_bytes': uploaded_file.size,
            }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            original_name = Path(uploaded_file.name or 'file').name
            safe_name = get_valid_filename(original_name)
            if not safe_name or safe_name in {'.', '..'}:
                return Response({'error': 'Некорректное имя файла.', 'code': 'invalid_filename'}, status=400)
            if len(safe_name) > 220:
                stem, extension = os.path.splitext(safe_name)
                safe_name = stem[:max(1, 220 - len(extension))] + extension[:32]

            today = timezone.localdate().strftime('%Y/%m/%d')
            file_path = f'uploads/{today}/{uuid.uuid4().hex}/{safe_name}'
            saved_path = default_storage.save(file_path, uploaded_file)
            return Response({
                'file_path': saved_path,
                'file_url': request.build_absolute_uri(settings.MEDIA_URL + saved_path),
                'file_name': original_name,
                'file_size': uploaded_file.size,
                'content_type': uploaded_file.content_type or 'application/octet-stream',
            }, status=status.HTTP_201_CREATED)
        except SuspiciousFileOperation:
            logger.warning('Rejected suspicious upload filename %r', uploaded_file.name)
            return Response({'error': 'Некорректное или небезопасное имя файла.', 'code': 'invalid_filename'}, status=400)
        except PermissionError:
            logger.exception('Upload directory is not writable')
            return Response({'error': 'Сервер не может записать файл. Проверьте права на каталог media.', 'code': 'storage_permission_denied'}, status=507)
        except OSError as exc:
            logger.exception('Storage error while uploading file')
            if exc.errno == errno.ENOSPC:
                return Response({'error': 'На сервере закончилось свободное место.', 'code': 'storage_full'}, status=507)
            return Response({'error': 'Ошибка файлового хранилища. Повторите попытку.', 'code': 'storage_error'}, status=507)
        except Exception:
            logger.exception('Unexpected file upload error')
            return Response({'error': 'Не удалось загрузить файл из-за внутренней ошибки.', 'code': 'upload_failed'}, status=500)

class SyncMessagesView(APIView):
    """
    Эндпоинт для запуска синхронизации сообщений (Polling)
    Работает в фоновом потоке, чтобы не блокировать веб-запрос
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        return Response(
            {'status': 'disabled', 'detail': 'Synchronization is managed by the connector process'},
            status=status.HTTP_410_GONE,
        )
