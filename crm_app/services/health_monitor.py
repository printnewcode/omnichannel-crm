"""
Сервис мониторинга здоровья системы и восстановления после ошибок
"""
import logging
import asyncio
from typing import Dict, List
from django.utils import timezone
from django.db import transaction
from ..models import TelegramAccount, Chat, Message
from .telegram_client_manager import TelegramClientManager

logger = logging.getLogger(__name__)


class HealthMonitor:
    """
    Сервис для мониторинга здоровья системы и автоматического восстановления
    """

    def __init__(self):
        self.client_manager = TelegramClientManager()
        self._monitoring_active = False
        self._check_interval = 60  # Проверка каждые 60 секунд

    async def start_monitoring(self):
        """Запуск фонового мониторинга"""
        if self._monitoring_active:
            return

        self._monitoring_active = True
        logger.info("Starting health monitoring service")

        while self._monitoring_active:
            try:
                await self._perform_health_checks()
            except Exception as e:
                logger.exception(f"Error in health monitoring: {e}")

            await asyncio.sleep(self._check_interval)

    def stop_monitoring(self):
        """Остановка мониторинга"""
        self._monitoring_active = False
        logger.info("Stopping health monitoring service")

    async def _perform_health_checks(self):
        """Выполнение проверок здоровья"""
        await self._check_database_connectivity()
        await self._check_telegram_clients()
        await self._check_failed_messages()
        await self._cleanup_old_data()

    async def _check_database_connectivity(self):
        """Проверка подключения к базе данных"""
        try:
            # Простая проверка - подсчет аккаунтов
            count = await self._async_count(TelegramAccount.objects.all())
            logger.debug(f"Database connectivity OK. Accounts count: {count}")
        except Exception as e:
            logger.error(f"Database connectivity failed: {e}")

    async def _check_telegram_clients(self):
        """Проверка состояния Telegram клиентов"""
        try:
            accounts = TelegramAccount.objects.filter(
                account_type=TelegramAccount.AccountType.PERSONAL,
                status=TelegramAccount.AccountStatus.ACTIVE
            )

            async for account in self._async_iterator(accounts):
                try:
                    await self._check_single_client(account)
                except Exception as e:
                    logger.error(f"Error checking client {account.id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Database error in _check_telegram_clients: {e}")
            return

    async def _check_single_client(self, account: TelegramAccount):
        """Проверка одного клиента"""
        try:
            # Проверка, запущен ли клиент
            if account.id not in self.client_manager._clients:
                logger.warning(f"Client for account {account.id} is not running, restarting...")
                success = await self.client_manager.start_client(account)
                if not success:
                    logger.error(f"Failed to restart client for account {account.id}")
                    account.status = TelegramAccount.AccountStatus.ERROR
                    account.last_error = "Failed to restart during health check"
                    account.error_count += 1
                    await self._async_save(account)
                return

            # Проверка, активно ли соединение
            client = self.client_manager._clients[account.id]
            if not client.is_connected:
                logger.warning(f"Client {account.id} disconnected, reconnecting...")
                await client.connect()

                if not client.is_connected:
                    logger.error(f"Failed to reconnect client {account.id}")
                    account.status = TelegramAccount.AccountStatus.ERROR
                    account.last_error = "Connection lost"
                    account.error_count += 1
                    await self._async_save(account)

            # Обновление времени последней активности
            account.last_activity = timezone.now()
            await self._async_save(account)

        except Exception as e:
            logger.exception(f"Error checking client {account.id}: {e}")

    async def _check_failed_messages(self):
        """Проверка и повторная отправка неудачных сообщений"""
        # Получение сообщений со статусом FAILED, созданных более 5 минут назад
        cutoff_time = timezone.now() - timezone.timedelta(minutes=5)

        failed_messages = Message.objects.filter(
            status='failed',  # Use string value directly to avoid model loading issues
            created_at__lt=cutoff_time
        ).select_related('chat', 'chat__telegram_account')[:10]  # Ограничение для предотвращения перегрузки

        async for message in self._async_iterator(failed_messages):
            await self._retry_failed_message(message)

    async def _retry_failed_message(self, message: Message):
        """Повторная попытка отправки неудачного сообщения"""
        from .message_router import MessageRouter

        try:
            router = MessageRouter()

            # Попытка повторной отправки
            telegram_message_id = await router.send_reply_async(
                message=message,
                text=message.text or "",
                media_path=message.media_file_path
            )

            if telegram_message_id:
                message.status = 'sent'
                message.telegram_id = telegram_message_id
                await self._async_save(message)
                logger.info(f"Successfully retried message {message.id}")
            else:
                # Если снова неудача, увеличиваем счетчик ошибок
                message.status = 'failed'
                await self._async_save(message)
                logger.warning(f"Failed to retry message {message.id}")

        except Exception as e:
            logger.exception(f"Error retrying message {message.id}: {e}")

    async def _cleanup_old_data(self):
        """Очистка старых данных"""
        try:
            # Удаление очень старых сообщений (старше 180 дней)
            cutoff_date = timezone.now() - timezone.timedelta(days=180)

            deleted_count = await self._async_delete(
                Message.objects.filter(
                    telegram_date__lt=cutoff_date,
                    message_type='text'  # Только текстовые сообщения
                )
            )

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old messages")

        except Exception as e:
            logger.exception(f"Error in cleanup: {e}")

    # Вспомогательные асинхронные методы для работы с БД

    async def _async_count(self, queryset):
        """Асинхронный подсчет"""
        from django.db import connection
        from asgiref.sync import sync_to_async

        return await sync_to_async(queryset.count)()

    async def _async_iterator(self, queryset):
        """Асинхронный итератор по queryset"""
        from asgiref.sync import sync_to_async
        from django.db import connection

        try:
            # Получаем все объекты синхронно, но оборачиваем в async
            objects = await sync_to_async(list)(queryset)
            for obj in objects:
                yield obj
        except Exception as e:
            logger.error(f"Database query failed in _async_iterator: {e}")
            # Try to close and reopen connection
            try:
                await sync_to_async(connection.close)()
                logger.info("Database connection closed due to error")
            except Exception as close_error:
                logger.warning(f"Failed to close connection: {close_error}")
            # Return empty iterator on error
            return

    async def _async_save(self, obj):
        """Асинхронное сохранение объекта"""
        from asgiref.sync import sync_to_async

        await sync_to_async(obj.save)()

    async def _async_delete(self, queryset):
        """Асинхронное удаление"""
        from asgiref.sync import sync_to_async

        result = await sync_to_async(queryset.delete)()
        return result[0] if result else 0

    # Методы для ручного управления

    async def force_restart_all_clients(self):
        """Принудительный перезапуск всех клиентов"""
        logger.info("Force restarting all clients...")
        await self.client_manager.stop_all()
        await asyncio.sleep(2)  # Небольшая задержка
        await self.client_manager.start_all_active()
        logger.info("Force restart completed")

    async def get_system_status(self) -> Dict:
        """Получение статуса системы"""
        try:
            # Сбор статистики
            accounts_count = await self._async_count(TelegramAccount.objects.all())
            active_accounts = await self._async_count(
                TelegramAccount.objects.filter(status=TelegramAccount.AccountStatus.ACTIVE)
            )
            running_clients = len(self.client_manager.get_running_accounts())
            total_chats = await self._async_count(Chat.objects.all())
            total_messages = await self._async_count(Message.objects.all())
            failed_messages = await self._async_count(
                Message.objects.filter(status='failed')
            )

            return {
                'status': 'healthy' if running_clients > 0 else 'warning',
                'accounts': {
                    'total': accounts_count,
                    'active': active_accounts,
                    'running_clients': running_clients
                },
                'chats': total_chats,
                'messages': {
                    'total': total_messages,
                    'failed': failed_messages
                },
                'monitoring_active': self._monitoring_active,
                'timestamp': timezone.now().isoformat()
            }

        except Exception as e:
            logger.exception(f"Error getting system status: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': timezone.now().isoformat()
            }