"""
Сервис для маршрутизации ответов на сообщения
Автоматически определяет, через какой клиент отправлять ответ (Telethon или telebot)
"""
import logging
import asyncio
from typing import Optional
from django.utils import timezone
from django.conf import settings
from ..models import Message as MessageModel, TelegramAccount, Chat
from .telegram_client_manager import TelegramClientManager

logger = logging.getLogger(__name__)


class MessageRouter:
    """
    Роутер сообщений для отправки ответов через правильный клиент
    """
    
    def __init__(self):
        self.client_manager = TelegramClientManager()
    
    async def send_reply_async(
        self,
        message: MessageModel,
        text: str,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Асинхронная отправка ответа на сообщение
        
        Args:
            message: Модель сообщения на которое отвечаем
            text: Текст ответа
            media_path: Путь к медиа файлу (опционально)
            
        Returns:
            int: Message ID если успешно, None если ошибка
        """
        try:
            chat = message.chat
            account = chat.telegram_account
            
            # Определение способа отправки
            if account.account_type == TelegramAccount.AccountType.PERSONAL:
                # Отправка через Telethon
                return await self._send_via_telethon(
                    account=account,
                    chat_id=chat.telegram_id,
                    text=text,
                    reply_to_message_id=message.telegram_id,
                    media_path=media_path
                )
            elif account.account_type == TelegramAccount.AccountType.BOT:
                # Отправка через Bot API (telebot webhook)
                return await self._send_via_bot_api(
                    account=account,
                    chat_id=chat.telegram_id,
                    text=text,
                    reply_to_message_id=message.telegram_id,
                    media_path=media_path
                )
            else:
                logger.error(f"Unknown account type: {account.account_type}")
                return None
                
        except Exception as e:
            logger.exception(f"Error sending reply: {e}")
            return None
    
    def send_reply(
        self,
        message: MessageModel,
        text: str,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Синхронная обёртка для отправки ответа
        
        Args:
            message: Модель сообщения на которое отвечаем
            text: Текст ответа
            media_path: Путь к медиа файлу (опционально)
            
        Returns:
            int: Message ID если успешно, None если ошибка
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self.send_reply_async(message, text, media_path)
            )
            loop.close()
            return result
        except Exception as e:
            logger.exception(f"Error in send_reply sync wrapper: {e}")
            return None
    
    async def _send_via_telethon(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Отправка через Telethon клиент

        Args:
            account: TelegramAccount модель
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
            media_path: Путь к медиа файлу

        Returns:
            int: Message ID если успешно
        """
        from telethon.errors import FloodWaitError, PeerIdInvalidError, ChatWriteForbiddenError
        import os

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                # Проверка, запущен ли клиент
                if account.id not in self.client_manager._clients:
                    logger.warning(f"Client for account {account.id} not running, starting...")
                    success = await self.client_manager.start_client(account)
                    if not success:
                        logger.error(f"Failed to start client for account {account.id}")
                        return None

                client = self.client_manager._clients[account.id]

                # Отправка сообщения
                if media_path:
                    # Проверка существования файла
                    if not os.path.exists(media_path):
                        logger.error(f"Media file not found: {media_path}")
                        return None
                    sent_message = await client.send_file(
                        chat_id,
                        media_path,
                        caption=text or None,
                        reply_to=reply_to_message_id
                    )
                else:
                    # Отправка текстового сообщения
                    sent_message = await client.send_message(
                        chat_id,
                        text,
                        reply_to=reply_to_message_id
                    )

                logger.info(f"Message sent successfully via Telethon: {sent_message.id}")
                return sent_message.id

            except FloodWaitError as e:
                wait_time = e.seconds
                logger.warning(f"FloodWait: waiting {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"FloodWait limit exceeded for account {account.id}")
                    return None

            except (PeerIdInvalidError, ChatWriteForbiddenError) as e:
                logger.error(f"Cannot send message to chat {chat_id}: {e}")
                return None

            except Exception as e:
                logger.exception(f"Error sending via Telethon (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                return None

        return None
    
    async def _send_via_bot_api(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Отправка через Bot API (для существующего бота)

        Args:
            account: TelegramAccount модель
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
            media_path: Путь к медиа файлу

        Returns:
            int: Message ID если успешно
        """
        import aiohttp
        import os

        if not account.bot_token:
            logger.error(f"No bot token for account {account.id}")
            return None

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                base_url = f"https://api.telegram.org/bot{account.bot_token}"

                async with aiohttp.ClientSession() as session:
                    if media_path:
                        # Проверка существования файла
                        if not os.path.exists(media_path):
                            logger.error(f"Media file not found: {media_path}")
                            return None

                        # Определение типа медиа
                        file_ext = os.path.splitext(media_path)[1].lower()

                        if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                            url = f"{base_url}/sendPhoto"
                            with open(media_path, 'rb') as f:
                                data = aiohttp.FormData()
                                data.add_field('chat_id', str(chat_id))
                                if text:
                                    data.add_field('caption', text)
                                if reply_to_message_id:
                                    data.add_field('reply_to_message_id', str(reply_to_message_id))
                                data.add_field('photo', f, filename=os.path.basename(media_path))
                        elif file_ext in ['.mp4', '.avi', '.mov', '.mkv']:
                            url = f"{base_url}/sendVideo"
                            with open(media_path, 'rb') as f:
                                data = aiohttp.FormData()
                                data.add_field('chat_id', str(chat_id))
                                if text:
                                    data.add_field('caption', text)
                                if reply_to_message_id:
                                    data.add_field('reply_to_message_id', str(reply_to_message_id))
                                data.add_field('video', f, filename=os.path.basename(media_path))
                        else:
                            # Отправка как документ
                            url = f"{base_url}/sendDocument"
                            with open(media_path, 'rb') as f:
                                data = aiohttp.FormData()
                                data.add_field('chat_id', str(chat_id))
                                if text:
                                    data.add_field('caption', text)
                                if reply_to_message_id:
                                    data.add_field('reply_to_message_id', str(reply_to_message_id))
                                data.add_field('document', f, filename=os.path.basename(media_path))

                        async with session.post(url, data=data) as response:
                            if response.status == 200:
                                result = await response.json()
                                if result.get('ok'):
                                    sent_message = result.get('result', {})
                                    logger.info(f"Message sent successfully via Bot API: {sent_message.get('message_id')}")
                                    return sent_message.get('message_id')
                                else:
                                    error_desc = result.get('description', 'Unknown error')
                                    logger.error(f"Bot API error: {error_desc}")

                                    # Специальная обработка частых ошибок
                                    if 'chat not found' in error_desc.lower():
                                        logger.error(f"Chat {chat_id} not found for bot {account.id}")
                                        return None
                                    elif 'bot was blocked' in error_desc.lower():
                                        logger.error(f"Bot {account.id} was blocked by user {chat_id}")
                                        return None
                            else:
                                logger.error(f"Bot API HTTP error: {response.status}")

                    else:
                        # Отправка текстового сообщения
                        url = f"{base_url}/sendMessage"
                        data = {
                            'chat_id': chat_id,
                            'text': text,
                        }
                        if reply_to_message_id:
                            data['reply_to_message_id'] = reply_to_message_id

                        async with session.post(url, json=data) as response:
                            if response.status == 200:
                                result = await response.json()
                                if result.get('ok'):
                                    sent_message = result.get('result', {})
                                    logger.info(f"Message sent successfully via Bot API: {sent_message.get('message_id')}")
                                    return sent_message.get('message_id')
                                else:
                                    error_desc = result.get('description', 'Unknown error')
                                    logger.error(f"Bot API error: {error_desc}")
                            else:
                                logger.error(f"Bot API HTTP error: {response.status}")

                # Если дошли сюда, значит ошибка
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                return None

            except aiohttp.ClientError as e:
                logger.exception(f"HTTP error sending via Bot API (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                    continue
                return None
            except Exception as e:
                logger.exception(f"Error sending via Bot API (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                    continue
                return None

        return None
    
    def create_outgoing_message(
        self,
        chat: Chat,
        text: str,
        telegram_message_id: int,
        reply_to_message: Optional[MessageModel] = None,
        message_type: str = 'text',
        media_file_path: Optional[str] = None
    ) -> MessageModel:
        """
        Создать запись об отправленном сообщении в БД
        
        Args:
            chat: Модель чата
            text: Текст сообщения
            telegram_message_id: ID сообщения в Telegram
            reply_to_message: Сообщение на которое отвечаем (опционально)
            message_type: Тип сообщения
            media_file_path: Путь к медиа файлу
            
        Returns:
            MessageModel: Созданная модель сообщения
        """
        return MessageModel.objects.create(
            telegram_id=telegram_message_id,
            chat=chat,
            text=text,
            message_type=message_type,
            status=MessageModel.MessageStatus.SENT,
            is_outgoing=True,
            telegram_date=timezone.now(),
            reply_to_message=reply_to_message,
            media_file_path=media_file_path,
            metadata={}
        )
