"""
Сигналы Django для автоматического создания/обновления связанных объектов
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Operator, Message, ChatAssignment, TelegramAccount
import asyncio


@receiver(post_save, sender=User)
def create_operator_profile(sender, instance, created, **kwargs):
    """Автоматически создавать профиль оператора при создании пользователя"""
    if created:
        Operator.objects.get_or_create(user=instance)


@receiver(post_save, sender=Message)
def update_chat_on_new_message(sender, instance, created, **kwargs):
    """Обновлять статистику чата при создании нового сообщения"""
    if created:
        chat = instance.chat
        chat.message_count = Message.objects.filter(chat=chat).count()
        chat.last_message_at = instance.telegram_date
        if not instance.is_outgoing:
            chat.unread_count = Message.objects.filter(
                chat=chat,
                is_outgoing=False
            ).count()
        chat.save(update_fields=['message_count', 'last_message_at', 'unread_count'])


@receiver(post_save, sender=TelegramAccount)
def auto_start_personal_account_auth(sender, instance, created, **kwargs):
    """
    Автоматически запускать процесс аутентификации для новых личных аккаунтов
    """
    if (created and
        instance.account_type == TelegramAccount.AccountType.PERSONAL and
        instance.phone_number and
        instance.api_id and
        instance.api_hash):

        # Установить статус "авторизация"
        instance.status = TelegramAccount.AccountStatus.AUTHENTICATING
        instance.save(update_fields=['status'])

        # Запустить асинхронную аутентификацию
        from asgiref.sync import async_to_sync
        from .services.telegram_client_manager import TelegramClientManager

        try:
            manager = TelegramClientManager()

            # Запустить аутентификацию в отдельной задаче
            def run_auth_async():
                # Use sync wrapper instead of async method
                result = manager.authenticate_account_sync(instance)
                if not result['success']:
                    instance.status = TelegramAccount.AccountStatus.ERROR
                    instance.last_error = result.get('error', 'Authentication failed')
                    instance.save(update_fields=['status', 'last_error'])

            # Start in background thread
            import threading
            thread = threading.Thread(target=run_auth_async, daemon=True)
            thread.start()

        except Exception as e:
            instance.status = TelegramAccount.AccountStatus.ERROR
            instance.last_error = f"Auto-auth failed: {str(e)}"
            instance.save(update_fields=['status', 'last_error'])
