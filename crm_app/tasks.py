"""
Celery задачи для асинхронной обработки медиа и сообщений
"""
import os
import logging
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.db.models import F
from typing import Optional
import requests
from datetime import datetime
from .models import Message, Chat, TelegramAccount, HistoryImportJob
from .services.realtime import publish_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_incoming_message(
    self,
    account_id: int,
    chat_id: int,
    telegram_message_id: int,
    telegram_date: str,
    text: Optional[str] = None,
    from_user_id: Optional[int] = None,
    from_user_name: Optional[str] = None,
    from_user_username: Optional[str] = None,
    is_outgoing: bool = False,
    reply_to_message_id: Optional[int] = None,
    message_type: str = 'text',
    media_file_id: Optional[str] = None,
    media_caption: Optional[str] = None
):
    """
    Обработка входящего сообщения и сохранение в БД
    
    Args:
        account_id: ID аккаунта
        chat_id: ID чата (из БД)
        telegram_message_id: ID сообщения в Telegram
        telegram_date: Дата сообщения (ISO format)
        text: Текст сообщения
        from_user_id: ID отправителя
        from_user_name: Имя отправителя
        from_user_username: Username отправителя
        is_outgoing: Исходящее ли сообщение
        reply_to_message_id: ID сообщения на которое отвечают
        message_type: Тип сообщения
        media_file_id: File ID медиа
        media_caption: Подпись к медиа
    """
    try:
        # Получение чата
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            logger.error(f"Chat {chat_id} not found")
            return
        
        # Поиск сообщения на которое отвечают
        reply_to_message = None
        if reply_to_message_id:
            try:
                reply_to_message = Message.objects.get(
                    telegram_id=reply_to_message_id,
                    chat=chat
                )
            except Message.DoesNotExist:
                logger.warning(f"Reply to message {reply_to_message_id} not found")
        
        # Парсинг даты
        from datetime import datetime
        try:
            message_date = datetime.fromisoformat(telegram_date.replace('Z', '+00:00'))
            if message_date.tzinfo is None:
                message_date = timezone.make_aware(message_date)
        except Exception as e:
            logger.warning(f"Error parsing date {telegram_date}: {e}")
            message_date = timezone.now()
        
        # Создание или обновление сообщения
        message, created = Message.objects.get_or_create(
            telegram_id=telegram_message_id,
            chat=chat,
            defaults={
                'text': text or media_caption,
                'message_type': message_type,
                'status': Message.MessageStatus.RECEIVED,
                'from_user_id': from_user_id,
                'from_user_name': from_user_name,
                'from_user_username': from_user_username,
                'is_outgoing': is_outgoing,
                'telegram_date': message_date,
                'reply_to_message': reply_to_message,
                'media_file_id': media_file_id,
                'media_caption': media_caption,
                'metadata': {}
            }
        )
        
        # Если сообщение уже существует, обновляем его
        if not created:
            message.text = text or media_caption or message.text
            message.message_type = message_type
            message.telegram_date = message_date
            message.save()
        
        # Если есть медиа, запускаем задачу на скачивание
        if created:
            Chat.objects.filter(pk=chat.pk).update(
                message_count=F('message_count') + 1,
                unread_count=F('unread_count') + (0 if is_outgoing else 1),
                last_message_at=message.telegram_date,
            )

        publish_message(message.id)
        if media_file_id and message_type in ['photo', 'video', 'voice', 'document', 'sticker']:
            download_media.delay(
                account_id=account_id,
                message_id=message.id,
                media_file_id=media_file_id,
                message_type=message_type
            )
        
        logger.info(f"Processed message {telegram_message_id} for chat {chat_id}")
        return message.id
        
    except Exception as e:
        logger.exception(f"Error processing incoming message: {e}")
        # Retry при ошибке
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def download_media(
    self,
    account_id: int,
    message_id: int,
    media_file_id: str,
    message_type: str
):
    """
    Скачать медиа файл из Telegram и сохранить локально
    
    Args:
        account_id: ID аккаунта
        message_id: ID сообщения в БД
        media_file_id: File ID медиа в Telegram
        message_type: Тип медиа (photo, video, voice, document)
    """
    try:
        # Получение сообщения
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            logger.error(f"Message {message_id} not found")
            return
        
        # Получение аккаунта
        try:
            account = TelegramAccount.objects.get(id=account_id)
        except TelegramAccount.DoesNotExist:
            logger.error(f"Account {account_id} not found")
            return
        
        # Создание директории для медиа
        media_dir = settings.MEDIA_ROOT / 'telegram' / message_type
        os.makedirs(media_dir, exist_ok=True)
        
        # Для Bot API используем getFile метод
        if account.account_type == TelegramAccount.AccountType.BOT:
            if not account.bot_token:
                logger.error(f"No bot token for account {account_id}")
                return
            
            # Получение информации о файле
            base_url = f"https://api.telegram.org/bot{account.bot_token}"
            file_info_url = f"{base_url}/getFile?file_id={media_file_id}"
            response = requests.get(file_info_url)
            
            if response.status_code != 200:
                logger.error(f"Failed to get file info: {response.status_code}")
                return
            
            file_info = response.json()
            if not file_info.get('ok'):
                logger.error(f"Bot API error: {file_info.get('description')}")
                return
            
            file_path = file_info['result']['file_path']
            file_url = f"https://api.telegram.org/file/bot{account.bot_token}/{file_path}"
            
            # Определение расширения файла
            file_ext = os.path.splitext(file_path)[1] or '.bin'
            
            # Скачивание файла
            file_response = requests.get(file_url, stream=True)
            if file_response.status_code == 200:
                # Сохранение файла
                local_filename = f"{message_id}_{media_file_id[:10]}{file_ext}"
                local_path = media_dir / local_filename
                
                with open(local_path, 'wb') as f:
                    for chunk in file_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Обновление сообщения
                message.media_file_path = str(local_path.relative_to(settings.MEDIA_ROOT))
                message.save(update_fields=['media_file_path'])
                
                logger.info(f"Downloaded media for message {message_id}: {local_path}")
            else:
                logger.error(f"Failed to download media: {file_response.status_code}")
                return
        
        # Для Telethon используем async загрузку через клиент
        # Это будет обработано в обработчике сообщений
        elif account.account_type == TelegramAccount.AccountType.PERSONAL:
            logger.warning("Media download for Telethon should be handled in message handler")
            # TODO: Реализовать загрузку через Telethon клиент
        
    except Exception as e:
        logger.exception(f"Error downloading media: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def download_green_api_media_task(self, message_id: int):
    """Download an incoming GREEN-API attachment outside the webhook request."""
    from .services.provider_media import download_green_api_media

    try:
        message = Message.objects.select_related('chat__telegram_account').get(id=message_id)
        if message.chat.telegram_account.account_type not in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
            return None
        result = download_green_api_media(message)
        publish_message(message.id)
        return result
    except Message.DoesNotExist:
        logger.warning('GREEN-API media message %s no longer exists', message_id)
        return None
    except Exception as exc:
        logger.exception('Could not download GREEN-API media for message %s', message_id)
        raise self.retry(exc=exc, countdown=min(300, 15 * (2 ** self.request.retries)))


@shared_task
def run_history_import(job_id: int):
    """Import history in a worker so web requests stay short and memory stays bounded."""
    from .services.history_import import (
        discover_green, discover_telegram, import_green_history, import_telegram_history,
    )

    job = HistoryImportJob.objects.select_related(
        'account', 'chat', 'chat__telegram_account',
    ).get(pk=job_id)
    job.status = HistoryImportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error = ''
    job.save(update_fields=['status', 'started_at', 'error'])
    try:
        media_ids = []
        if job.kind == HistoryImportJob.Kind.CHAT_HISTORY:
            requested_count = job.parameters.get('count')
            count = max(1, min(int(requested_count), 10000)) if requested_count else None
            job.progress_total = count if requested_count else None
            job.save(update_fields=['progress_total'])
            if job.account.account_type == TelegramAccount.AccountType.PERSONAL:
                # Telethon streams iter_messages, so "all" does not build the
                # complete history in RAM.
                created, processed = import_telegram_history(job.chat, count)
            elif job.account.account_type in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
                # GREEN-API returns one response rather than pages. Keep that
                # response bounded on the small production VPS.
                created, processed, media_ids = import_green_history(job.chat, count or 10000)
            else:
                raise ValueError('Загрузка истории для Telegram-ботов недоступна.')
            result = {'created_messages': created, 'processed_messages': processed}
            job.progress_current = processed
        else:
            since = datetime.fromisoformat(job.parameters['since']) if job.parameters.get('since') else None
            if since and timezone.is_naive(since):
                since = timezone.make_aware(since)
            if job.account.account_type == TelegramAccount.AccountType.PERSONAL:
                discovered, imported = discover_telegram(job.account, since, per_chat=5)
            elif job.account.account_type in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
                discovered, imported, media_ids, provider_stats = discover_green(job.account, since, per_chat=5)
            else:
                raise ValueError('Загрузка диалогов для Telegram-ботов недоступна.')
            result = {'created_chats': discovered, 'created_messages': imported}
            if job.account.account_type in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
                result.update(provider_stats)
            job.progress_current = discovered

        for message_id in media_ids:
            download_green_api_media_task.delay(message_id)
        job.status = HistoryImportJob.Status.COMPLETED
        job.result = result
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'result', 'progress_current', 'finished_at'])
        return result
    except Exception as exc:
        logger.exception('History import job %s failed', job_id)
        job.status = HistoryImportJob.Status.FAILED
        job.error = str(exc)[:2000]
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error', 'finished_at'])
        raise

@shared_task
def cleanup_old_messages():
    """
    Периодическая задача для очистки старых сообщений
    Можно настроить в Celery Beat
    """
    from django.utils import timezone
    from datetime import timedelta
    
    # Удаление сообщений старше 90 дней
    cutoff_date = timezone.now() - timedelta(days=90)
    
    deleted_count, _ = Message.objects.filter(
        telegram_date__lt=cutoff_date,
        message_type='text'
    ).delete()
    
    logger.info(f"Cleaned up {deleted_count} old messages")
    return deleted_count
