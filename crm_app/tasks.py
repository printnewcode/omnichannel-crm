"""
Celery задачи для асинхронной обработки медиа и сообщений
"""
import os
import logging
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from typing import Optional
import requests
from .models import Message, Chat, TelegramAccount

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
        if media_file_id and message_type in ['photo', 'video', 'voice', 'document']:
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
        media_dir = settings.BASE_DIR / 'media' / 'telegram' / message_type
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
                message.media_file_path = str(local_path.relative_to(settings.BASE_DIR))
                message.save(update_fields=['media_file_path'])
                
                logger.info(f"Downloaded media for message {message_id}: {local_path}")
            else:
                logger.error(f"Failed to download media: {file_response.status_code}")
                return
        
        # Для Hydrogram используем async загрузку через клиент
        # Это будет обработано в обработчике сообщений
        elif account.account_type == TelegramAccount.AccountType.PERSONAL:
            logger.warning("Media download for Hydrogram should be handled in message handler")
            # TODO: Реализовать загрузку через Hydrogram клиент
        
    except Exception as e:
        logger.exception(f"Error downloading media: {e}")
        raise self.retry(exc=e, countdown=60)


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
