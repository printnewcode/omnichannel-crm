"""
Менеджер для управления множественными Telethon клиентами
Обрабатывает динамическое создание, запуск и остановку клиентов
"""
import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Dict, Optional, List
import socks
from django.conf import settings
from django.utils import timezone

from django.utils.text import get_valid_filename

from django.db import close_old_connections
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from telethon import TelegramClient, events, functions, types, utils
from telethon.network.connection.tcpobfuscated import ConnectionTcpObfuscated
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    RPCError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
    PhoneNumberUnoccupiedError,
    ApiIdInvalidError,
)
from ..models import TelegramAccount
from .message_content import telegram_forward_info, telegram_special_content

logger = logging.getLogger(__name__)


class TelegramClientManager:
    """
    Singleton менеджер для управления несколькими Telethon клиентами
    Работает в асинхронном режиме внутри Django
    """
    
    _instance: Optional['TelegramClientManager'] = None
    _clients: Dict[int, TelegramClient] = {}
    _qr_logins: Dict[int, dict] = {}
    _tasks: Dict[int, asyncio.Task] = {}
    _catchup_tasks: Dict[int, asyncio.Task] = {}
    _last_sync_time: Dict[int, float] = {}  # Throttle for on-demand sync
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _loop_thread: Optional[threading.Thread] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._lock = asyncio.Lock()
            self._media_download_locks = {}
    
    async def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """Получить или создать event loop"""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def _ensure_background_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure a persistent background loop for long-lived clients."""
        # Using a thread-safe check for the loop
        if self._loop and self._loop.is_running():
            return self._loop

        # Use a threading lock to prevent multiple loops from starting
        if not hasattr(self, '_loop_lock'):
            self._loop_lock = threading.Lock()
            
        with self._loop_lock:
            if self._loop and self._loop.is_running():
                return self._loop

            # On Windows, SelectorEventLoop is often more stable for Telethon in a background thread
            if os.name == 'nt':
                try:
                    self._loop = asyncio.SelectorEventLoop()
                except Exception:
                    self._loop = asyncio.new_event_loop()
            else:
                self._loop = asyncio.new_event_loop()

            def runner(loop: asyncio.AbstractEventLoop):
                asyncio.set_event_loop(loop)
                try:
                    loop.run_forever()
                except Exception as e:
                    logger.error(f"Error in Telethon background loop: {e}")
                finally:
                    # Cleanup loop on exit
                    try:
                        loop.close()
                    except:
                        pass

            self._loop_thread = threading.Thread(target=runner, args=(self._loop,), daemon=True, name="TelethonManagerLoop")
            self._loop_thread.start()
            
            # Give the loop a moment to start
            time.sleep(0.1)
            
        return self._loop

    def run_async_sync(self, coro):
        """Run a coroutine in the background loop and wait for result"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def _create_client(self, session, api_id: int, api_hash: str) -> TelegramClient:
        """Create a Telethon client with Docker-friendly connection defaults."""
        proxy = None
        proxy_url = getattr(settings, 'TELEGRAM_PROXY_URL', '').strip()
        if proxy_url:
            parsed = urlparse(proxy_url)
            proxy_types = {
                'socks5': socks.SOCKS5,
                'socks5h': socks.SOCKS5,
                'socks4': socks.SOCKS4,
                'http': socks.HTTP,
                'https': socks.HTTP,
            }
            proxy_type = proxy_types.get(parsed.scheme.lower())
            if not proxy_type or not parsed.hostname or not parsed.port:
                raise ValueError(
                    'TELEGRAM_PROXY_URL must use socks5, socks4 or http and include host:port'
                )
            proxy = (
                proxy_type,
                parsed.hostname,
                parsed.port,
                True,
                unquote(parsed.username) if parsed.username else None,
                unquote(parsed.password) if parsed.password else None,
            )
        return TelegramClient(
            session,
            api_id,
            api_hash,
            connection=ConnectionTcpObfuscated,
            connection_retries=5,
            retry_delay=2,
            timeout=20,
            auto_reconnect=True,
            proxy=proxy,
        )
    
    def start_client_sync(self, account: TelegramAccount) -> bool:
        """Sync wrapper for start_client"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.start_client(account), loop)
        return future.result()
    
    async def start_client(self, account: TelegramAccount) -> bool:
        """
        Запустить Telethon клиент для аккаунта
        
        Args:
            account: TelegramAccount модель
            
        Returns:
            bool: True если успешно запущен
        """
        if account.account_type != TelegramAccount.AccountType.PERSONAL:
            logger.error(f"Account {account.id} is not a personal account")
            return False
        
        if account.id in self._clients:
            logger.warning(f"Client for account {account.id} already running")
            return True
        
        if not account.session_string and account.status != TelegramAccount.AccountStatus.AUTHENTICATING:
            logger.error(f"No session string for account {account.id}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = "Отсутствует session string. Требуется авторизация"
            await database_sync_to_async(account.save)()
            return False
        
        try:
            # Создание клиента Telethon
            client = self._create_client(
                StringSession(account.session_string),
                account.api_id,
                account.api_hash,
            )

            await client.connect()
            if not await client.is_user_authorized():
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = "Сессия недействительна. Требуется повторная авторизация"
                account.session_string = None
                await database_sync_to_async(account.save)()
                await client.disconnect()
                return False
            
            # Получение информации о пользователе
            me = await client.get_me()
            account.telegram_user_id = me.id
            account.first_name = me.first_name
            account.last_name = me.last_name
            account.username = me.username
            account.status = TelegramAccount.AccountStatus.ACTIVE
            account.last_activity = timezone.now()
            account.last_error = None
            account.error_count = 0

            # Сохранение session string (Telethon)
            account.session_string = client.session.save()

            await database_sync_to_async(account.save)()

            # Регистрация обработчиков (входящие и исходящие)
            client.add_event_handler(
                self._create_message_handler(account),
                events.NewMessage()
            )
            client.add_event_handler(
                self._create_edit_handler(account),
                events.MessageEdited()
            )
            client.add_event_handler(
                self._create_reaction_handler(account),
                events.Raw(types.UpdateMessageReactions),
            )
            
            # Сохранение клиента и запуск задачи прослушивания
            self._clients[account.id] = client
            
            # Запуск задачи для обработки обновлений
            loop = await self._get_or_create_loop()
            task = loop.create_task(self._listen_updates(client, account))
            self._tasks[account.id] = task
            
            logger.info(f"Successfully started client for account {account.id}")
            return True
            
        except AuthKeyUnregisteredError:
            logger.error(f"Auth key unregistered for account {account.id}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = "Сессия недействительна. Требуется повторная авторизация"
            account.session_string = None  # Сброс сессии
            await database_sync_to_async(account.save)()
            return False
        except UserDeactivatedError:
            logger.error(f"User deactivated for account {account.id}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = "Аккаунт деактивирован"
            await database_sync_to_async(account.save)()
            return False
        except FloodWaitError as e:
            logger.warning(f"FloodWait for account {account.id}: {e.seconds} seconds")
            account.last_error = f"FloodWait: {e.seconds} секунд"
            await database_sync_to_async(account.save)()
            return False
        except Exception as e:
            logger.exception(f"Error starting client for account {account.id}: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            account.error_count += 1
            await database_sync_to_async(account.save)()
            return False
    
    def stop_client_sync(self, account_id: int) -> bool:
        """Sync wrapper for stop_client"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.stop_client(account_id), loop)
        return future.result()
    
    async def stop_client(self, account_id: int, *, mark_inactive: bool = True) -> bool:
        """
        Остановить клиент для аккаунта
        
        Args:
            account_id: ID аккаунта
            
        Returns:
            bool: True если успешно остановлен
        """
        if account_id not in self._clients:
            logger.warning(f"Client for account {account_id} is not running")
            return True
        
        try:
            # Остановка задачи прослушивания
            if account_id in self._tasks:
                task = self._tasks[account_id]
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                del self._tasks[account_id]
            
            # Остановка клиента
            client = self._clients[account_id]
            await client.disconnect()
            
            del self._clients[account_id]
            
            # A process restart must not disable an account in persistent configuration.
            if mark_inactive:
                try:
                    account = await sync_to_async(TelegramAccount.objects.get)(id=account_id)
                    account.status = TelegramAccount.AccountStatus.INACTIVE
                    await database_sync_to_async(account.save)()
                except TelegramAccount.DoesNotExist:
                    pass
            
            logger.info(f"Successfully stopped client for account {account_id}")
            return True
            
        except Exception as e:
            logger.exception(f"Error stopping client for account {account_id}: {e}")
            return False

    def send_message_sync(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Синхронная обертка для отправки сообщения через запущенный клиент

        Args:
            account_id: ID аккаунта
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
            media_path: Путь к медиа файлу (опционально)

        Returns:
            int: Message ID если успешно, None если ошибка
        """
        try:
            loop = self._ensure_background_loop()
            future = asyncio.run_coroutine_threadsafe(
                self.send_message(account_id, chat_id, text, reply_to_message_id, media_path),
                loop
            )
            return future.result(timeout=60) # Increased timeout for media
        except Exception as e:
            logger.exception(f"Error in send_message_sync: {e}")
            return None

    def send_reaction_sync(self, account_id: int, chat_id: int, message_id: int, emoji: str) -> bool:
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(
            self.send_reaction(account_id, chat_id, message_id, emoji), loop
        )
        return bool(future.result(timeout=30))

    async def send_reaction(self, account_id: int, chat_id: int, message_id: int, emoji: str) -> bool:
        client = self._clients.get(account_id)
        if not client:
            logger.error('Client for account %s not running', account_id)
            return False
        try:
            await client(functions.messages.SendReactionRequest(
                peer=chat_id,
                msg_id=int(message_id),
                reaction=[types.ReactionEmoji(emoticon=emoji)],
                big=False,
            ))
            return True
        except RPCError as exc:
            logger.error('Telegram reaction failed for message %s: %s', message_id, exc)
            return False
    
    async def send_message(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        media_path: Optional[str] = None
    ) -> Optional[int]:
        """
        Отправить сообщение через Telethon клиент
        
        Args:
            account_id: ID аккаунта
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
            media_path: Путь к медиа файлу (опционально)
            
        Returns:
            int: Message ID если успешно, None если ошибка
        """
        if account_id not in self._clients:
            logger.error(f"Client for account {account_id} not running")
            return None
            
        client = self._clients[account_id]
        
        try:
            if media_path:
                from django.conf import settings
                import os
                
                # DEBUG: Log path details
                logger.info(f"DEBUG: Resolving media_path: '{media_path}'")
                logger.info(f"DEBUG: MEDIA_ROOT: '{settings.MEDIA_ROOT}'")
                try:
                    logger.info(f"DEBUG: CWD: '{os.getcwd()}'")
                except:
                    pass

                # Если путь относительный, добавляем MEDIA_ROOT
                if not os.path.isabs(media_path):
                    # Remove 'media/' prefix if present
                    clean_path = media_path
                    if media_path.startswith('media/') or media_path.startswith('/media/') or media_path.startswith('\\media\\'):
                         clean_path = media_path.replace('media/', '', 1).replace('\\media\\', '', 1).lstrip('/\\')
                         
                    full_media_path = os.path.join(settings.MEDIA_ROOT, clean_path)
                else:
                    full_media_path = media_path
                
                logger.info(f"DEBUG: Final full_media_path: '{full_media_path}'")
                    
                if not os.path.exists(full_media_path):
                    logger.error(f"Media file not found at: {full_media_path}")
                    # Try one more fallback: relative to CWD
                    fallback_path = os.path.abspath(media_path)
                    logger.info(f"DEBUG: Trying fallback path: '{fallback_path}'")
                    if os.path.exists(fallback_path):
                        full_media_path = fallback_path
                    else:
                        return None
                
                sent_message = await client.send_file(
                    chat_id,
                    full_media_path,
                    caption=text or None,
                    reply_to=reply_to_message_id
                )
            else:
                sent_message = await client.send_message(
                    chat_id,
                    text,
                    reply_to=reply_to_message_id
                )
            
            # Обновление последней активности
            try:
                account = await sync_to_async(TelegramAccount.objects.get)(id=account_id)
                account.last_activity = timezone.now()
                await database_sync_to_async(account.save)(update_fields=['last_activity'])
            except TelegramAccount.DoesNotExist:
                pass
            
            return sent_message.id
            
        except FloodWaitError as e:
            logger.warning(f"FloodWait when sending message: {e.seconds} seconds")
            # Можно добавить retry с задержкой
            await asyncio.sleep(e.seconds)
            return await self.send_message(account_id, chat_id, text, reply_to_message_id, media_path)
        except RPCError as e:
            logger.error(
                "Telegram RPC error while sending message: %s (code=%s, value=%s)",
                e.__class__.__name__,
                getattr(e, "code", None),
                getattr(e, "value", None),
            )
            return None
        except Exception as e:
            logger.exception(f"Error sending message: {e}")
            return None
    
    def _create_message_handler(self, account: TelegramAccount):
        """Создать обработчик сообщений для аккаунта (Telethon)"""
        from channels.layers import get_channel_layer
        
        async def handle_message(event):
            """Обработка входящих сообщений"""
            from ..models import Chat, Message as MessageModel
            from .reactions import telegram_reaction_summary
            from channels.db import database_sync_to_async

            message = event.message

            # CRM принимает только обычные личные диалоги и группы, но не каналы.
            if not (message.is_private or message.is_group):
                return

            try:
                # Получение чата и отправителя
                chat_entity = await event.get_chat()
                sender_entity = await event.get_sender()

                # Определение типа чата
                if message.is_private:
                    chat_type = 'private'
                elif message.is_group:
                    chat_type = 'group'
                elif message.is_channel:
                    chat_type = 'channel'
                else:
                    chat_type = 'unknown'
                peer_metadata = self._telegram_peer_metadata(chat_entity)
                peer_phone = getattr(chat_entity, 'phone', None) or getattr(sender_entity, 'phone', None)
                if peer_phone:
                    peer_metadata = {**peer_metadata, 'contact_phone': str(peer_phone)}

                # Получение или создание чата
                @database_sync_to_async
                def get_or_create_chat():
                    chat, created = Chat.objects.get_or_create(
                        telegram_id=message.chat_id,
                        telegram_account=account,
                        defaults={
                            'chat_type': chat_type,
                            'title': getattr(chat_entity, 'title', None),
                            'username': getattr(chat_entity, 'username', None),
                            'first_name': getattr(chat_entity, 'first_name', None),
                            'last_name': getattr(chat_entity, 'last_name', None),
                            'metadata': peer_metadata,
                            'is_bot': chat_type == 'private' and bool(getattr(chat_entity, 'bot', False)),
                        }
                    )

                    # Обновление информации о чате
                    updated = False
                    if hasattr(chat_entity, 'title') and chat_entity.title != chat.title:
                        chat.title = chat_entity.title
                        updated = True
                    if hasattr(chat_entity, 'username') and chat_entity.username != chat.username:
                        chat.username = chat_entity.username
                        updated = True
                    peer_is_bot = chat_type == 'private' and bool(getattr(chat_entity, 'bot', False))
                    if chat.is_bot != peer_is_bot:
                        chat.is_bot = peer_is_bot
                        updated = True
                    if peer_metadata and not (chat.metadata or {}).get('telegram_peer'):
                        chat.metadata = {**(chat.metadata or {}), **peer_metadata}
                        updated = True

                    if updated or created:
                        chat.save()

                    return chat, created

                chat, chat_created = await get_or_create_chat()
                if peer_phone and not chat_created:
                    from .google_contacts import match_chat_contact
                    await database_sync_to_async(match_chat_contact)(chat)

                # Определение типа сообщения и медиа
                message_type = self._get_message_type(message) or 'text'
                media_file_id = self._get_media_file_id(message)

                logger.info(f"Processing message {message.id}: type={message_type}, has_media={bool(message.media)}, photo={bool(getattr(message, 'photo', None))}, video={bool(getattr(message, 'video', None))}")

                # Создание записи сообщения в БД
                @database_sync_to_async
                def create_message_record():
                    from django.db import IntegrityError
                    # Поиск сообщения на которое отвечают
                    reply_to_message = None
                    if message.reply_to_msg_id:
                        try:
                            reply_to_message = MessageModel.objects.get(
                                telegram_id=message.reply_to_msg_id,
                                chat=chat
                            )
                        except MessageModel.DoesNotExist:
                            pass

                    # Telegram file_id не нужен, скачиваем по message.telegram_id

                    # Создание сообщения (дедупликация по telegram_id + chat)
                    try:
                        message_obj, message_created = MessageModel.objects.get_or_create(
                            telegram_id=message.id,
                            chat=chat,
                            defaults={
                                'text': message.message or None,
                                'message_type': message_type,
                                'status': MessageModel.MessageStatus.RECEIVED,
                                'from_user_id': getattr(sender_entity, 'id', None),
                                'from_user_name': getattr(sender_entity, 'first_name', None),
                                'from_user_username': getattr(sender_entity, 'username', None),
                                'is_outgoing': message.out,
                                'telegram_date': message.date,
                                'reply_to_message': reply_to_message,
                                'media_file_id': media_file_id,
                                'media_caption': getattr(message, 'message', None) if message_type != 'text' else None,
                                'metadata': {
                                    'reactions': telegram_reaction_summary(getattr(message, 'reactions', None)),
                                    'special_content': telegram_special_content(message),
                                    'forward_info': telegram_forward_info(message),
                                }
                            }
                        )
                    except IntegrityError:
                        # В случае гонки или других ошибок дублирования
                        try:
                            message_obj = MessageModel.objects.get(
                                telegram_id=message.id,
                                chat=chat
                            )
                        except MessageModel.DoesNotExist:
                            # Если сообщение всё же не существует, пропускаем
                            return None, False

                    return message_obj, message_created

                message_obj, message_created = await create_message_record()

                # Если сообщение не удалось создать/получить, пропускаем обработку
                if message_obj is None:
                    return

                # Download through the already connected account so entity access hashes
                # and the correct Telegram session are available.
                if message.media and not message_obj.media_file_path:
                    await self._download_media_telethon(event.client, message, message_obj)

                # Обновление статистики чата
                @database_sync_to_async
                def update_chat_stats():
                    chat.message_count += 1
                    chat.last_message_at = message.date
                    if not message.out:
                        chat.unread_count += 1
                    chat.save(update_fields=['message_count', 'last_message_at', 'unread_count'])

                if message_created:
                    await update_chat_stats()

                # Telegram file_id уже сохранен при создании сообщения

                # Единый realtime-канал: чат сразу видят все операторы.
                from .realtime import publish_message
                await database_sync_to_async(publish_message)(message_obj.id)
                from .ai_assistant import register_incoming_message, register_provider_outgoing
                if message_created:
                    if message_obj.is_outgoing:
                        await database_sync_to_async(register_provider_outgoing)(message_obj.id)
                    else:
                        await database_sync_to_async(register_incoming_message)(message_obj.id)
                logger.info(f"Processed incoming message {message.id} for chat {chat.id}")
                
            except Exception as e:
                logger.exception(f"Error handling message: {e}")
        
        return handle_message

    def _create_reaction_handler(self, account: TelegramAccount):
        async def handle_reaction(update):
            from ..models import Chat, Message as MessageModel
            from .reactions import set_reaction_summary, telegram_reaction_summary
            from .realtime import publish_message

            try:
                chat_id = utils.get_peer_id(update.peer)
                message = await database_sync_to_async(
                    MessageModel.objects.filter(
                        chat__telegram_account=account,
                        chat__telegram_id=chat_id,
                        telegram_id=update.msg_id,
                    ).first
                )()
                if not message:
                    return
                summary = telegram_reaction_summary(update.reactions)
                await database_sync_to_async(set_reaction_summary)(message.id, summary)
                await database_sync_to_async(publish_message)(message.id)
            except Exception:
                logger.exception('Failed to process Telegram reaction update for account %s', account.id)

        return handle_reaction
    
    def _get_message_type(self, message) -> str:
        """Определить тип сообщения (Telethon)"""
        if message.photo:
            return 'photo'
        if message.video:
            return 'video'
        if getattr(message, 'voice', None):
            return 'voice'
        if getattr(message, 'audio', None):
            return 'audio'
        if message.sticker:
            return 'sticker'
        if message.document:
            return 'document'
        if getattr(message, 'geo', None):
            return 'location'
        if getattr(message, 'contact', None):
            return 'contact'
        if getattr(message, 'poll', None):
            return 'poll'
        if getattr(message, 'action', None):
            return 'service'
        media = getattr(message, 'media', None)
        if media and media.__class__.__name__ == 'MessageMediaDice':
            return 'other'
            
        return 'text'
    
    def _get_media_file_id(self, message) -> Optional[str]:
        """Telethon не предоставляет file_id как в Bot API"""
        return None
    
    def _telegram_media_filename(self, message, message_type: str) -> str:
        original = getattr(getattr(message, 'file', None), 'name', None)
        if original:
            safe = get_valid_filename(Path(original).name)
            if safe:
                return safe[:220]
        extension = self._get_file_extension(message, message_type)
        labels = {'photo': 'photo', 'video': 'video', 'voice': 'voice', 'audio': 'audio', 'sticker': 'sticker'}
        return f"{labels.get(message_type, 'file')}_{message.id}{extension}"

    async def _download_media_telethon(self, client: TelegramClient, message, message_obj):
        """Download Telegram media with the connected account and preserve its name."""
        try:
            file_name = self._telegram_media_filename(message, message_obj.message_type)
            relative_dir = Path('telegram') / message_obj.message_type / str(message_obj.id)
            media_dir = Path(settings.MEDIA_ROOT) / relative_dir
            media_dir.mkdir(parents=True, exist_ok=True)
            local_path = media_dir / file_name

            downloaded = await client.download_media(message, file=str(local_path))
            if not downloaded or not local_path.is_file():
                raise RuntimeError('Telegram не вернул содержимое файла.')

            relative_path = (relative_dir / file_name).as_posix()
            message_obj.media_file_path = relative_path
            message_obj.metadata = {
                **(message_obj.metadata or {}),
                'original_filename': file_name,
                'media_size': local_path.stat().st_size,
            }
            await database_sync_to_async(message_obj.save)(
                update_fields=['media_file_path', 'metadata', 'updated_at']
            )
            logger.info('Downloaded Telegram media for message %s to %s', message_obj.id, local_path)
            return relative_path
        except Exception:
            logger.exception('Error downloading Telegram media for message %s', message_obj.id)
            return None

    def _get_file_extension(self, message, message_type: str) -> str:
        if message_type == 'photo':
            return '.jpg'
        if message_type == 'video':
            return '.mp4'
        if message_type in {'voice', 'audio'}:
            return '.ogg'
        telethon_extension = getattr(getattr(message, 'file', None), 'ext', None)
        if telethon_extension:
            return telethon_extension
        original = getattr(getattr(message, 'file', None), 'name', None)
        if original:
            return Path(original).suffix or '.bin'
        return '.bin'
    def _get_telegram_file_id(self, message) -> Optional[str]:
        """Получить file_id из сообщения Telegram для ленивой загрузки"""
        try:
            logger.info(f"Getting file_id for message media: {type(message.media)}")
            if hasattr(message.media, 'file_id'):
                logger.info(f"Found file_id: {message.media.file_id}")
                return message.media.file_id
            elif hasattr(message.media, 'id'):
                logger.info(f"Found id: {message.media.id}")
                return str(message.media.id)
            elif hasattr(message.media, 'file_ref'):
                logger.info(f"Found file_ref: {message.media.file_ref}")
                return message.media.file_ref
            else:
                logger.warning(f"No file_id/id/file_ref found in media object: {dir(message.media)}")
        except Exception as e:
            logger.exception(f"Error getting file_id: {e}")
        return None

    def download_media_by_message_id_sync(self, message, timeout: int = 180) -> Optional[str]:
        """Sync версия для скачивания медиа по message.telegram_id"""
        # Pass only the primary key across the sync/async boundary. The object
        # returned by the API queryset contains deferred fields; touching one
        # from the Telethon loop would make Django perform synchronous ORM I/O
        # in an async context.
        message_id = message.pk
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._download_with_fresh_client(message_id), loop,
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError('Telegram не успел подготовить файл. Попробуйте ещё раз позже.')

    def _download_in_background(self, message_id: int) -> Optional[str]:
        """Выполнить скачивание в фоне"""
        try:
            return self.run_async_sync(self._download_with_fresh_client(message_id))
        except Exception as e:
            logger.error(f"Error in background download: {e}")
            raise

    @staticmethod
    def _telegram_download_context(message_id: int) -> dict:
        """Load every ORM value needed by async Telegram media download."""
        from ..models import Message

        message = Message.objects.select_related('chat__telegram_account').get(pk=message_id)
        account = message.chat.telegram_account
        return {
            'message_id': message.id,
            'telegram_message_id': message.telegram_id,
            'message_type': message.message_type,
            'metadata': dict(message.metadata or {}),
            'chat_telegram_id': message.chat.telegram_id,
            'chat_id': message.chat.id,
            'chat_type': message.chat.chat_type,
            'chat_username': message.chat.username or '',
            'chat_metadata': dict(message.chat.metadata or {}),
            'account_id': account.id,
            'session_string': account.session_string or '',
            'api_id': account.api_id,
            'api_hash': account.api_hash,
        }

    @staticmethod
    def _store_downloaded_media(message_id: int, relative_path: str, metadata: dict) -> None:
        from ..models import Message

        Message.objects.filter(pk=message_id).update(
            media_file_path=relative_path,
            metadata=metadata,
            updated_at=timezone.now(),
        )

    @staticmethod
    def _telegram_peer_metadata(entity) -> dict:
        access_hash = getattr(entity, 'access_hash', None)
        if access_hash is None:
            return {}
        if isinstance(entity, (types.User, types.InputPeerUser)):
            peer_type = 'user'
        elif isinstance(entity, (types.Channel, types.InputPeerChannel)):
            peer_type = 'channel'
        else:
            return {}
        return {'telegram_peer': {'type': peer_type, 'access_hash': str(access_hash)}}

    @staticmethod
    def _store_telegram_peer(chat_id: int, peer_metadata: dict) -> None:
        from ..models import Chat

        if not peer_metadata:
            return
        chat = Chat.objects.only('id', 'metadata').get(pk=chat_id)
        metadata = dict(chat.metadata or {})
        metadata.update(peer_metadata)
        Chat.objects.filter(pk=chat_id).update(metadata=metadata)

    async def _resolve_download_peer(self, client, context: dict):
        peer = context['chat_metadata'].get('telegram_peer') or {}
        access_hash = peer.get('access_hash')
        if access_hash not in (None, ''):
            if peer.get('type') == 'channel':
                return types.InputPeerChannel(context['chat_telegram_id'], int(access_hash))
            return types.InputPeerUser(context['chat_telegram_id'], int(access_hash))
        if context['chat_type'] == 'group':
            return types.InputPeerChat(context['chat_telegram_id'])
        if context['chat_username']:
            entity = await client.get_entity(context['chat_username'])
        else:
            entity = None
            # Telegram can resolve contacts and users who have messaged the
            # account using access_hash=0. This avoids walking thousands of
            # dialogs for the common private-chat case.
            try:
                entity = await client.get_input_entity(
                    types.PeerUser(context['chat_telegram_id'])
                )
            except (ValueError, RPCError):
                entity = None
            # Existing records may predate persisted access hashes. Scan only
            # until the requested peer is found, then cache the hash in the DB.
            if entity is None:
                async for dialog in client.iter_dialogs():
                    if getattr(dialog.entity, 'id', None) == context['chat_telegram_id']:
                        entity = dialog.entity
                        break
            if entity is None:
                raise LookupError('Диалог не найден в подключённом Telegram-аккаунте.')
        peer_metadata = self._telegram_peer_metadata(entity)
        if peer_metadata:
            await database_sync_to_async(self._store_telegram_peer)(
                context['chat_id'], peer_metadata,
            )
        return entity

    async def _download_with_fresh_client(self, message_or_id) -> Optional[str]:
        """Скачать медиа используя новый клиент"""
        from telethon.sessions import StringSession

        message_id = getattr(message_or_id, 'pk', message_or_id)
        context = await database_sync_to_async(self._telegram_download_context)(message_id)

        lock = self._media_download_locks.setdefault(context['account_id'], asyncio.Lock())
        await lock.acquire()
        client = self._create_client(
            StringSession(context['session_string']),
            context['api_id'],
            context['api_hash'],
        )

        try:
            await client.connect()
            logger.info(
                'Downloading media for message %s (telegram_id: %s) in chat %s',
                context['message_id'], context['telegram_message_id'], context['chat_telegram_id'],
            )

            chat_entity = await self._resolve_download_peer(client, context)
            telegram_message = await client.get_messages(chat_entity, ids=[context['telegram_message_id']])

            if not telegram_message or not telegram_message[0]:
                raise Exception(
                    f"Message {context['telegram_message_id']} not found in chat {context['chat_telegram_id']}"
                )

            telegram_message = telegram_message[0]

            if not telegram_message.media:
                raise Exception(f"Message {context['telegram_message_id']} has no media")

            file_name = self._telegram_media_filename(telegram_message, context['message_type'])
            relative_dir = Path('telegram') / context['message_type'] / str(context['message_id'])
            media_dir = Path(settings.MEDIA_ROOT) / relative_dir
            media_dir.mkdir(parents=True, exist_ok=True)
            local_path = media_dir / file_name

            downloaded = await client.download_media(telegram_message, file=str(local_path))
            if not downloaded or not local_path.is_file():
                raise RuntimeError('Telegram не вернул содержимое файла.')

            relative_path = (relative_dir / file_name).as_posix()
            metadata = {
                **context['metadata'],
                'original_filename': file_name,
                'media_size': local_path.stat().st_size,
            }
            await database_sync_to_async(self._store_downloaded_media)(
                context['message_id'], relative_path, metadata
            )

            logger.info(f"Successfully downloaded media to {local_path}")
            return relative_path

        finally:
            try:
                await client.disconnect()
            finally:
                lock.release()

    async def download_media_by_message_id(self, message) -> Optional[str]:
        """Скачать медиа по message.telegram_id для ленивой загрузки"""
        message_id = getattr(message, 'pk', message)
        context = await database_sync_to_async(self._telegram_download_context)(message_id)
        client = self._clients.get(context['account_id'])

        if not client or not client.is_connected():
            raise Exception("No active Telegram client available")

        try:
            logger.info(
                'Downloading media for message %s (telegram_id: %s) in chat %s',
                context['message_id'], context['telegram_message_id'], context['chat_telegram_id'],
            )

            chat_entity = await client.get_entity(context['chat_telegram_id'])
            logger.info(f"Got chat entity: {chat_entity}")

            telegram_message = await client.get_messages(chat_entity, ids=[context['telegram_message_id']])
            logger.info(f"Got messages: {len(telegram_message) if telegram_message else 0}")

            if not telegram_message or not telegram_message[0]:
                raise Exception(
                    f"Message {context['telegram_message_id']} not found in chat {context['chat_telegram_id']}"
                )

            telegram_message = telegram_message[0]
            logger.info(f"Message has media: {bool(telegram_message.media)}")

            if not telegram_message.media:
                raise Exception(f"Message {context['telegram_message_id']} has no media")

            file_name = self._telegram_media_filename(telegram_message, context['message_type'])
            relative_dir = Path('telegram') / context['message_type'] / str(context['message_id'])
            media_dir = Path(settings.MEDIA_ROOT) / relative_dir
            media_dir.mkdir(parents=True, exist_ok=True)
            local_path = media_dir / file_name

            downloaded = await client.download_media(telegram_message, file=str(local_path))
            if not downloaded or not local_path.is_file():
                raise RuntimeError('Telegram не вернул содержимое файла.')

            relative_path = (relative_dir / file_name).as_posix()
            metadata = {
                **context['metadata'],
                'original_filename': file_name,
                'media_size': local_path.stat().st_size,
            }
            await database_sync_to_async(self._store_downloaded_media)(
                context['message_id'], relative_path, metadata
            )

            return relative_path

        except Exception as e:
            logger.exception(f"Error downloading media by message_id: {e}")
            raise

    def _get_file_extension_from_message_type(self, message_type: str) -> str:
        """Получить расширение файла по типу сообщения"""
        if message_type == 'photo':
            return '.jpg'
        elif message_type == 'video':
            return '.mp4'
        elif message_type == 'voice':
            return '.ogg'
        elif message_type == 'document':
            return '.bin'
        return '.bin'

    def _get_sent_code_type(self, sent_code) -> tuple[str, Optional[str]]:
        """Получить тип отправленного кода (Telethon)"""
        def map_type(code_type):
            name = code_type.__class__.__name__.lower()
            if "sms" in name:
                return "SMS"
            if "app" in name:
                return "APP"
            if "flash" in name:
                return "FLASH_CALL"
            if "call" in name:
                return "CALL"
            return code_type.__class__.__name__

        code_type = map_type(sent_code.type)
        next_type = map_type(sent_code.next_type) if getattr(sent_code, 'next_type', None) else None
        return code_type, next_type
    
    async def _listen_updates(self, client: TelegramClient, account: TelegramAccount):
        """Задача для прослушивания обновлений"""
        try:
            logger.info(f"Listening for updates on account {account.id}")
            # Ask Telegram for updates missed while this session was offline.
            await client.catch_up()
            # Клиент будет прослушивать обновления автоматически
            # Эта задача просто держит соединение активным
            
            # Catch up on missed messages
            try:
                # Store catchup task separately so we can wait for it
                # Telegram's catch_up() has already replayed missed updates. The
                # history pass is only a safety net and must skip dialogs whose
                # latest message is already stored; forcing it here caused a
                # costly scan after every connector restart.
                self._catchup_tasks[account.id] = asyncio.create_task(
                    self._catch_up_history(client, account, force=False)
                )
                await self._catchup_tasks[account.id]
            except Exception as e:
                 logger.error(f"Failed to catch up history for {account.id}: {e}")
            finally:
                self._catchup_tasks.pop(account.id, None)
                 
            while True:
                await asyncio.sleep(60)  # Проверка каждую минуту
                # Ensure connection is fresh before checking Telegram
                await database_sync_to_async(close_old_connections)()
                if not client.is_connected():
                    logger.warning(f"Client {account.id} disconnected, reconnecting...")
                    await client.connect()
                    if await client.is_user_authorized():
                        await client.catch_up()
                        await self.sync_messages_for_account(client, account, force=False)
        except asyncio.CancelledError:
            logger.info(f"Stopped listening for account {account.id}")
            raise
        except Exception as e:
            logger.exception(f"Error in listen_updates for account {account.id}: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            await database_sync_to_async(account.save)()
    
    def authenticate_account_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for authenticate_account"""
        return self.run_async_sync(self.authenticate_account(account))

    async def authenticate_account(self, account: TelegramAccount) -> dict:
        """
        Начать процесс авторизации аккаунта

        Args:
            account: TelegramAccount для авторизации

        Returns:
            dict: Результат авторизации
        """
        if account.account_type != TelegramAccount.AccountType.PERSONAL:
            return {
                'success': False,
                'error': 'Only personal accounts can be authenticated'
            }

        # Validate phone number format
        if not account.phone_number:
            return {
                'success': False,
                'error': 'Phone number is required for authentication'
            }

        # Basic phone number validation (should start with + and have digits)
        import re
        phone_pattern = r'^\+\d{7,15}$'
        if not re.match(phone_pattern, account.phone_number):
            return {
                'success': False,
                'error': f'Invalid phone number format. Must be in international format starting with +, e.g. +79123456789. Current: {account.phone_number}'
            }

        # Validate API credentials
        if not account.api_id or not account.api_hash:
            return {
                'success': False,
                'error': 'API ID and API Hash are required for authentication'
            }

        if account.id in self._clients:
            await self.stop_client(account.id, mark_inactive=False)

        # Ensure any stale temporary sessions are logged out
        if account.pending_session_string:
            try:
                stale_client = self._create_client(StringSession(account.pending_session_string), account.api_id, account.api_hash)
                await stale_client.connect()
                if not await stale_client.is_user_authorized():
                    logger.info(f"Logging out stale temporary session for {account.id}")
                    await stale_client.log_out()
                await stale_client.disconnect()
            except:
                pass

        logger.info(f"Starting authentication for account {account.id} with phone {account.phone_number}")

        try:
            # Создание клиента Telethon (временная сессия)
            client = self._create_client(StringSession(), account.api_id, account.api_hash)

            # Запуск процесса авторизации
            account.status = TelegramAccount.AccountStatus.AUTHENTICATING
            account.last_error = None
            await database_sync_to_async(account.save)()

            await client.connect()

            try:
                if not await client.is_user_authorized():
                    # Отправка кода на номер телефона
                    sent_code = await client.send_code_request(account.phone_number)

                    code_type, next_type = self._get_sent_code_type(sent_code)

                    # Сохраняем временную сессию и phone_code_hash для верификации
                    account.pending_session_string = client.session.save()
                    account.pending_session_name = None
                    account.pending_phone_code_hash = sent_code.phone_code_hash
                    account.pending_code_sent_at = timezone.now()
                    account.pending_code_type = code_type
                    await database_sync_to_async(account.save)()

                    # Формируем сообщение в зависимости от типа кода
                    if code_type == 'SMS':
                        message = 'OTP код отправлен по SMS на ваш номер телефона'
                        if next_type:
                            message += f'. Если SMS не пришел, следующий метод: {next_type}'
                    elif code_type == 'APP':
                        message = 'OTP код отправлен в Telegram приложение'
                    elif code_type == 'CALL':
                        message = 'Вам поступит звонок с кодом'
                    elif code_type == 'FLASH_CALL':
                        message = 'Вам поступит пропущенный звонок (код в номере)'
                    else:
                        message = f'OTP код отправлен через {code_type}'

                    return {
                        'success': True,
                        'status': 'otp_required',
                        'message': message,
                        'phone_code_hash': sent_code.phone_code_hash,
                        'code_type': code_type,
                        'next_type': next_type,
                        'timeout': getattr(sent_code, 'timeout', None)
                    }

                # Уже авторизован
                session_string = client.session.save()
                account.session_string = session_string
                account.status = TelegramAccount.AccountStatus.ACTIVE

                # Получение информации о пользователе
                me = await client.get_me()
                account.telegram_user_id = me.id
                account.first_name = me.first_name
                account.last_name = me.last_name
                account.username = me.username
                await database_sync_to_async(account.save)()

                return {
                    'success': True,
                    'status': 'authenticated',
                    'message': 'Account already authenticated'
                }

            except FloodWaitError as e:
                wait_seconds = e.seconds
                wait_hours = wait_seconds // 3600
                wait_minutes = (wait_seconds % 3600) // 60
                if wait_hours > 0:
                    readable_wait = f"{wait_hours} часов {wait_minutes} минут"
                else:
                    readable_wait = f"{wait_minutes} минут"
                user_message = f"Превышен лимит запросов Telegram. Подождите {readable_wait} перед следующей попыткой."
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = user_message
                await database_sync_to_async(account.save)()
                return {'success': False, 'error': user_message}
            except PhoneNumberInvalidError:
                user_message = "Неверный номер телефона. Проверьте формат номера (должен начинаться с + и содержать только цифры)."
            except PhoneNumberBannedError:
                user_message = "Этот номер телефона заблокирован в Telegram."
            except PhoneNumberUnoccupiedError:
                user_message = "Этот номер телефона не зарегистрирован в Telegram."
            except ApiIdInvalidError:
                user_message = "Неверный API ID. Проверьте настройки API."
            except Exception as e:
                logger.exception(f"Error in authenticate_account: {e}")
                user_message = f"Ошибка отправки кода: {str(e)}"

            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = user_message
            await database_sync_to_async(account.save)()
            return {
                'success': False,
                'error': user_message
            }
        except Exception as e:
            logger.exception(f"Error in authentication: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            await database_sync_to_async(account.save)()
            return {
                'success': False,
                'error': str(e)
            }
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass  # Already disconnected or connection error

    def verify_otp_sync(self, account: TelegramAccount, otp_code: str, password: str = None) -> dict:
        """Sync wrapper for verify_otp"""
        return self.run_async_sync(self.verify_otp(account, otp_code, password))

    async def verify_otp(self, account: TelegramAccount, otp_code: str, password: str = None) -> dict:
        """
        Завершить авторизацию с OTP кодом

        Args:
            account: TelegramAccount
            otp_code: Код из Telegram
            password: Пароль для 2FA (опционально)

        Returns:
            dict: Результат верификации
        """
        if account.account_type != TelegramAccount.AccountType.PERSONAL:
            return {
                'success': False,
                'error': 'Only personal accounts can be verified'
            }

        # Извлечение phone_code_hash из pending-полей
        if not account.pending_phone_code_hash or not account.pending_session_string:
            return {
                'success': False,
                'error': 'Аутентификация не запущена. Сначала нажмите "Начать аутентификацию".'
            }

        phone_code_hash = account.pending_phone_code_hash

        try:
            # Создание клиента для верификации (Telethon)
            logger.info(f"Creating verification client for account {account.id}")
            client = self._create_client(
                StringSession(account.pending_session_string),
                account.api_id,
                account.api_hash
            )

            await client.connect()

            try:
                logger.info(f"Client connected successfully for account {account.id} verification")
                logger.info(f"Attempting sign-in with stored hash for account {account.id}")
                await client.sign_in(
                    phone=account.phone_number,
                    code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                logger.info(f"Sign-in successful for account {account.id}")
            except SessionPasswordNeededError:
                if not password:
                    return {
                        'success': False,
                        'error': 'Требуется пароль 2FA. Введите пароль и попробуйте снова.'
                    }
                await client.sign_in(password=password)
            except PhoneCodeInvalidError:
                error_message = "Неверный код подтверждения. Проверьте код и попробуйте снова."
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = f"OTP verification failed: {error_message}"
                await database_sync_to_async(account.save)()
                return {'success': False, 'error': error_message}
            except PhoneCodeExpiredError:
                error_message = (
                    "Код подтверждения истек или был заменен новым. "
                    "Нажмите «Отправить код повторно» и используйте последний код."
                )
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = f"OTP verification failed: {error_message}"
                await database_sync_to_async(account.save)()
                return {'success': False, 'error': error_message}
            except Exception as sign_in_error:
                error_message = str(sign_in_error)
                if "connection" in error_message.lower() or "network" in error_message.lower():
                    error_message = "Проблема с подключением к Telegram. Проверьте интернет-соединение."
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = f"OTP verification failed: {error_message}"
                await database_sync_to_async(account.save)()
                return {'success': False, 'error': error_message}

            # Успешная авторизация
            session_string = client.session.save()

            # Получение информации о пользователе
            me = await client.get_me()
            account.telegram_user_id = me.id
            account.first_name = me.first_name
            account.last_name = me.last_name
            account.username = me.username
            account.session_string = session_string
            account.pending_session_string = None
            account.pending_session_name = None
            account.pending_phone_code_hash = None
            account.pending_code_sent_at = None
            account.pending_code_type = None
            account.status = TelegramAccount.AccountStatus.ACTIVE
            account.last_error = None
            account.error_count = 0
            await database_sync_to_async(account.save)()

            logger.info(f"Successfully authenticated account {account.id}")
            await client.disconnect()
            return {
                'success': True,
                'status': 'authenticated',
                'message': 'Account successfully authenticated',
                'session_string': session_string
            }

        except Exception as e:
            logger.exception(f"Error in OTP verification: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            await database_sync_to_async(account.save)()
            return {
                'success': False,
                'error': f"Verification failed: {str(e)}"
            }

    async def send_verification_code(self, account: TelegramAccount) -> dict:
        """Send a fresh code for verification (without verifying)"""
        if not account.phone_number:
            return {
                'success': False,
                'error': 'Phone number not found'
            }

        try:
            # Create client for sending verification code (Telethon)
            session = StringSession(account.pending_session_string) if account.pending_session_string else StringSession()
            client = self._create_client(session, account.api_id, account.api_hash)

            await client.connect()

            try:
                # Send new code
                sent_code = await client.send_code_request(account.phone_number)

                code_type, _ = self._get_sent_code_type(sent_code)

                # Update pending auth data with new hash and timestamp
                account.pending_session_string = client.session.save()
                account.pending_session_name = None
                account.pending_phone_code_hash = sent_code.phone_code_hash
                account.pending_code_sent_at = timezone.now()
                account.pending_code_type = code_type
                await database_sync_to_async(account.save)()

                await client.disconnect()
                return {
                    'success': True,
                    'message': f'OTP код отправлен через {code_type}',
                    'code_type': code_type
                }

            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        except FloodWaitError as e:
            wait_seconds = e.seconds
            wait_hours = wait_seconds // 3600
            wait_minutes = (wait_seconds % 3600) // 60
            if wait_hours > 0:
                readable_wait = f"{wait_hours} часов {wait_minutes} минут"
            else:
                readable_wait = f"{wait_minutes} минут"
            user_message = f"Превышен лимит запросов Telegram. Подождите {readable_wait} перед следующей попыткой."
            return {'success': False, 'error': user_message}
        except Exception as e:
            logger.exception(f"Error sending verification code: {e}")
            return {
                'success': False,
                'error': f"Не удалось отправить код верификации: {str(e)}"
            }

    def send_verification_code_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for send_verification_code"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.send_verification_code(account), loop)
        return future.result()

    async def resend_code(self, account: TelegramAccount) -> dict:
        """
        Resend OTP code using a different verification method

        Args:
            account: TelegramAccount that needs code resent

        Returns:
            dict: Result of resend operation
        """
        if not account.phone_number:
            return {
                'success': False,
                'error': 'Phone number not found'
            }

        # Check if authentication is in progress
        if not account.pending_phone_code_hash:
            return {
                'success': False,
                'error': 'No pending authentication found'
            }

        current_hash = account.pending_phone_code_hash

        try:
            session = StringSession(account.pending_session_string) if account.pending_session_string else StringSession()
            client = self._create_client(session, account.api_id, account.api_hash)

            await client.connect()

            try:
                # Prefer resend_code_request to switch delivery method if possible
                if account.pending_phone_code_hash:
                    try:
                        sent_code = await client.resend_code_request(
                            account.phone_number,
                            account.pending_phone_code_hash
                        )
                    except Exception:
                        sent_code = await client.send_code_request(account.phone_number)
                else:
                    sent_code = await client.send_code_request(account.phone_number)

                code_type, next_type = self._get_sent_code_type(sent_code)

                # Update pending auth data with new hash and timestamp
                account.pending_session_string = client.session.save()
                account.pending_session_name = None
                account.pending_phone_code_hash = sent_code.phone_code_hash
                account.pending_code_sent_at = timezone.now()
                account.pending_code_type = code_type
                await database_sync_to_async(account.save)()

                # Form message based on code type
                if code_type == 'SMS':
                    message = 'OTP код отправлен по SMS повторно'
                elif code_type == 'APP':
                    message = 'OTP код отправлен в Telegram приложение'
                elif code_type == 'CALL':
                    message = 'Вам поступит звонок с кодом'
                elif code_type == 'FLASH_CALL':
                    message = 'Вам поступит пропущенный звонок (код в номере)'
                else:
                    message = f'OTP код отправлен через {code_type}'

                return {
                    'success': True,
                    'status': 'otp_resent',
                    'message': message,
                    'phone_code_hash': sent_code.phone_code_hash,
                    'code_type': code_type,
                    'next_type': next_type,
                    'timeout': getattr(sent_code, 'timeout', None)
                }

            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        except FloodWaitError as e:
            return {
                'success': False,
                'error': f"Превышен лимит запросов Telegram. Подождите {e.seconds} секунд."
            }
        except Exception as e:
            logger.exception(f"Error in resend_code: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def resend_code_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for resend_code"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.resend_code(account), loop)
        return future.result()

    def restart_client_sync(self, account_id: int) -> bool:
        """Sync wrapper for restart_client"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.restart_client(account_id), loop)
        return future.result()
    
    async def restart_client(self, account_id: int) -> bool:
        """Перезапустить клиент"""
        await self.stop_client(account_id, mark_inactive=False)
        try:
            account = TelegramAccount.objects.get(id=account_id)
            return await self.start_client(account)
        except TelegramAccount.DoesNotExist:
            return False
    
    def get_running_accounts(self) -> List[int]:
        """Получить список ID запущенных аккаунтов"""
        return list(self._clients.keys())
    
    async def create_qr_login(self, account: TelegramAccount) -> dict:
        """Создать QR login для Telethon (async wrapper)"""
        return self.create_qr_login_sync(account)

    async def check_qr_login(self, account: TelegramAccount, password: str | None = None) -> dict:
        """Проверить статус QR login (async wrapper)"""
        return self.check_qr_login_sync(account, password)

    async def _cleanup_qr_login(self, account_id: int) -> None:
        entry = self._qr_logins.pop(account_id, None)
        if entry:
            try:
                if entry.get('thread') and entry['thread'].is_alive():
                    entry['cancel'] = True
            except Exception:
                pass

    def create_qr_login_sync(self, account: TelegramAccount) -> dict:
        """Sync creation of QR login using a dedicated background thread"""
        if account.account_type != TelegramAccount.AccountType.PERSONAL:
            return {'success': False, 'error': 'Only personal accounts can be authenticated'}

        # Stop previous QR login flow if any
        entry = self._qr_logins.get(account.id)
        if entry:
            entry['cancel'] = True

        entry = {
            'status': 'pending',
            'qr_url': None,
            'error': None,
            'session_string': None,
            'password': None,
            'password_event': threading.Event(),
            'qr_ready_event': threading.Event(),
            'cancel': False,
        }
        self._qr_logins[account.id] = entry

        async def mark_error(message: str):
            entry['status'] = 'error'
            entry['error'] = message
            entry['qr_ready_event'].set()
            try:
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = message
                await database_sync_to_async(account.save)(update_fields=['status', 'last_error'])
            except Exception:
                logger.exception("Failed to persist QR login error for account %s", account.id)

        def runner():
            close_old_connections()

            async def run_qr():
                client = None
                try:
                    # Stopping existing client if any to avoid multiple connections
                    if account.id in self._clients:
                        logger.info(f"Stopping existing client for account {account.id} before new QR login")
                        await self.stop_client(account.id, mark_inactive=False)

                    client = self._create_client(StringSession(), account.api_id, account.api_hash)
                    await client.connect()

                    qr_login = await client.qr_login()
                    entry['qr_url'] = qr_login.url
                    entry['status'] = 'pending'
                    entry['qr_ready_event'].set()

                    try:
                        await qr_login.wait()
                    except SessionPasswordNeededError:
                        entry['status'] = 'password_required'
                        entry['password_event'].wait(timeout=300)
                        if entry.get('cancel'):
                            return
                        if not entry.get('password'):
                            await mark_error('2FA пароль не был введен вовремя.')
                            return
                        try:
                            await client.sign_in(password=entry['password'])
                        except Exception as e:
                            if "password" in str(e).lower():
                                logger.info(f"QR Login: Wrong password for {account.id}. Logging out session.")
                                try:
                                    await client.log_out()
                                except Exception:
                                    pass
                            raise

                    if entry.get('cancel'):
                        return

                    if await client.is_user_authorized():
                        session_string = client.session.save()
                        entry['session_string'] = session_string
                        entry['status'] = 'authenticated'

                        me = await client.get_me()
                        account.telegram_user_id = me.id
                        account.first_name = me.first_name
                        account.last_name = me.last_name
                        account.username = me.username
                        account.session_string = session_string
                        account.pending_session_string = None
                        account.pending_session_name = None
                        account.pending_phone_code_hash = None
                        account.pending_code_sent_at = None
                        account.pending_code_type = None
                        account.status = TelegramAccount.AccountStatus.ACTIVE
                        account.last_error = None
                        account.error_count = 0
                        await database_sync_to_async(account.save)()

                        # QR authentication runs in the web process, while live
                        # Telegram updates are owned by the dedicated connector.
                        # Keeping this authenticated client connected here creates
                        # two connections with the same auth key. Telegram may then
                        # deliver updates to this client, which has no event handlers,
                        # until an API call on the connector wakes its connection.
                        # Persist the StringSession and let the connector reconcile it.
                    else:
                        entry['status'] = 'pending'

                except Exception as e:
                    raw_error = str(e) or e.__class__.__name__
                    is_proxy_error = (
                        'ProxyConnectionError' in e.__class__.__name__
                        or 'proxy' in raw_error.lower()
                        or (
                            getattr(settings, 'TELEGRAM_PROXY_URL', '').strip()
                            and 'Connection to Telegram failed' in raw_error
                        )
                    )
                    is_connection_closed = '0 bytes read' in raw_error or e.__class__.__name__ == 'IncompleteReadError'
                    if is_proxy_error:
                        user_error = (
                            'Не удалось подключиться к Telegram через прокси из TELEGRAM_PROXY_URL. '
                            'Проверьте, что прокси запущен и доступен контейнеру. '
                            'Для локального прокси Docker Desktop обычно используется '
                            'socks5://host.docker.internal:<порт>.'
                        )
                        logger.warning(
                            'Telegram proxy connection failed for account %s: %s',
                            account.id,
                            raw_error,
                        )
                    elif is_connection_closed:
                        user_error = (
                            'Telegram закрыл MTProto-соединение до создания QR. '
                            'Чаще всего это временная сетевая проблема Docker/VPN/провайдера. '
                            'Нажмите "Обновить QR" еще раз; если повторяется, проверьте доступ контейнера к Telegram.'
                        )
                        logger.warning("Telegram closed QR login connection for account %s: %s", account.id, raw_error)
                    else:
                        user_error = raw_error
                        logger.exception(f"Error creating QR login for account {account.id}: {raw_error}")
                    await mark_error(user_error)
                    try:
                        if client and not await client.is_user_authorized():
                            await client.log_out()
                    except Exception:
                        pass
                finally:
                    # The QR client is only an authorization transport. It must
                    # never remain connected after the session has been persisted;
                    # the connector process is the single owner of live updates.
                    if client:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass

            try:
                self.run_async_sync(run_qr())
            except Exception as e:
                logger.exception(f"Unhandled QR login runner error for account {account.id}: {e}")
                entry['status'] = 'error'
                entry['error'] = str(e) or e.__class__.__name__
                entry['qr_ready_event'].set()
                try:
                    account.status = TelegramAccount.AccountStatus.ERROR
                    account.last_error = entry['error']
                    account.save(update_fields=['status', 'last_error'])
                except Exception:
                    logger.exception("Failed to persist QR login runner error for account %s", account.id)
            finally:
                close_old_connections()

        thread = threading.Thread(target=runner, daemon=True, name=f"TelegramQRLogin-{account.id}")
        entry['thread'] = thread
        thread.start()

        account.status = TelegramAccount.AccountStatus.AUTHENTICATING
        account.last_error = None
        account.save(update_fields=['status', 'last_error'])

        # Wait briefly for QR URL or connection error to be ready.
        entry['qr_ready_event'].wait(timeout=30)

        if entry.get('status') == 'error':
            self._qr_logins.pop(account.id, None)
            return {
                'success': False,
                'status': 'error',
                'error': entry.get('error') or 'Не удалось создать QR login',
            }

        return {
            'success': True,
            'status': 'qr_required',
            'qr_url': entry.get('qr_url'),
        }
    def check_qr_login_sync(self, account: TelegramAccount, password: str | None = None) -> dict:
        """Sync status check for QR login"""
        entry = self._qr_logins.get(account.id)
        if not entry:
            # If account already activated, return authenticated
            if account.status == TelegramAccount.AccountStatus.ACTIVE:
                return {'success': True, 'status': 'authenticated'}
            return {'success': False, 'error': 'QR login not initialized'}

        if entry.get('status') == 'password_required' and password:
            entry['password'] = password
            entry['password_event'].set()
            # Give the worker a short time to finalize auth after 2FA submit.
            start = time.monotonic()
            while time.monotonic() - start < 8:
                if entry.get('status') == 'authenticated' and entry.get('session_string'):
                    break
                if entry.get('status') == 'error':
                    break
                time.sleep(0.2)

        if entry.get('status') == 'authenticated' and entry.get('session_string'):
            session_string = entry['session_string']
            # Финализируем сохранение
            account.session_string = session_string
            account.pending_session_string = None
            account.pending_session_name = None
            account.pending_phone_code_hash = None
            account.pending_code_sent_at = None
            account.pending_code_type = None
            account.status = TelegramAccount.AccountStatus.ACTIVE
            account.last_error = None
            account.error_count = 0
            account.save()
            self._qr_logins.pop(account.id, None)
            return {'success': True, 'status': 'authenticated'}

        if entry.get('status') == 'error':
            error = entry.get('error', 'Unknown error')
            self._qr_logins.pop(account.id, None)
            return {'success': False, 'error': error}

        # For normal "check" requests, wait briefly to avoid requiring a second click.
        if entry.get('status') in {'pending', 'password_required'}:
            start = time.monotonic()
            while time.monotonic() - start < 3:
                if entry.get('status') == 'authenticated' and entry.get('session_string'):
                    break
                if entry.get('status') == 'error':
                    break
                time.sleep(0.2)

        if entry.get('status') == 'authenticated' and entry.get('session_string'):
            session_string = entry['session_string']
            account.session_string = session_string
            account.pending_session_string = None
            account.pending_session_name = None
            account.pending_phone_code_hash = None
            account.pending_code_sent_at = None
            account.pending_code_type = None
            account.status = TelegramAccount.AccountStatus.ACTIVE
            account.last_error = None
            account.error_count = 0
            account.save()
            self._qr_logins.pop(account.id, None)
            return {'success': True, 'status': 'authenticated'}

        if entry.get('status') == 'error':
            error = entry.get('error', 'Unknown error')
            self._qr_logins.pop(account.id, None)
            return {'success': False, 'error': error}

        return {'success': True, 'status': entry.get('status', 'pending'), 'qr_url': entry.get('qr_url')}
    
    async def stop_all(self):
        """Остановить все клиенты"""
        account_ids = list(self._clients.keys())
        for account_id in account_ids:
            await self.stop_client(account_id, mark_inactive=False)

    def restart_all_clients_sync(self):
        """Sync wrapper to restart all clients"""
        loop = self._ensure_background_loop()
        asyncio.run_coroutine_threadsafe(self.force_restart_all_sync(), loop)

    async def force_restart_all_sync(self):
        """Force restart all clients (async version)"""
        await self.stop_all()
        await asyncio.sleep(1)
        await self.start_all_active()

    async def wait_for_catchups(self):
        """Wait for all running catchup tasks to finish"""
        if not self._catchup_tasks:
            return
        
        logger.info(f"Waiting for {len(self._catchup_tasks)} catchup tasks...")
        await asyncio.gather(*self._catchup_tasks.values(), return_exceptions=True)
        logger.info("All catchup tasks finished or failed")

    def wait_for_catchups_sync(self, timeout=60):
        """Sync wrapper to wait for catchups"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.wait_for_catchups(), loop)
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            logger.error(f"Error waiting for catchups: {e}")
            return False

    def check_authorization_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for check_authorization"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.check_authorization(account), loop)
        return future.result()

    async def check_authorization(self, account: TelegramAccount) -> dict:
        """
        Check if the session is still valid by connecting a temporary client
        """
        if not account.session_string:
            return {'success': False, 'error': 'No session string found'}

        client = self._create_client(
                StringSession(account.session_string),
                account.api_id,
                account.api_hash,
            )
        try:
            await client.connect()
            is_authorized = await client.is_user_authorized()
            if is_authorized:
                # Update account info if needed
                me = await client.get_me()
                account.telegram_user_id = me.id
                account.username = me.username
                account.status = TelegramAccount.AccountStatus.ACTIVE
                account.last_error = None
                await database_sync_to_async(account.save)()
                return {'success': True, 'authorized': True}
            else:
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = "Сессия недействительна (отозвана или истекла)"
                await database_sync_to_async(account.save)()
                return {'success': True, 'authorized': False}
        finally:
            await client.disconnect()

    def _create_edit_handler(self, account: TelegramAccount):
        """Создать обработчик редактирования сообщений"""
        
        async def handle_edit(event):
            """Обработка отредактированных сообщений"""
            from ..models import Chat, Message as MessageModel
            from channels.db import database_sync_to_async

            message = event.message
            
            try:
                # Получение чата
                chat_entity = await event.get_chat()
                
                # Найти существующее сообщение в БД
                @database_sync_to_async
                def update_message():
                    try:
                        # Находим чат
                        chat = Chat.objects.get(
                            telegram_id=message.chat_id,
                            telegram_account=account
                        )
                        
                        # Находим сообщение
                        msg_obj = MessageModel.objects.get(
                            telegram_id=message.id,
                            chat=chat
                        )
                        
                        # Обновляем текст
                        msg_obj.text = message.message
                        msg_obj.save(update_fields=['text'])
                        return msg_obj
                    except (Chat.DoesNotExist, MessageModel.DoesNotExist):
                        return None

                updated_msg = await update_message()
                
                if updated_msg:
                    logger.info(f"Message {message.id} edited")
                    # (Optional) Notify websocket about edit
            except Exception as e:
                logger.exception(f"Error handling edited message: {e}")

        return handle_edit

    async def _catch_up_history(self, client: TelegramClient, account_or_id, force=False):
        """
        Загрузить пропущенные сообщения (история пока клиент был офлайн)
        """
        from ..models import Chat, Message as MessageModel, TelegramAccount
        from channels.db import database_sync_to_async
        from asgiref.sync import sync_to_async
        from django.db.models import Max
        import logging
        from django.utils import timezone
        
        # Resolve account and its ID safely
        if isinstance(account_or_id, int):
            account_id = account_or_id
            account = await sync_to_async(TelegramAccount.objects.get)(id=account_id)
        else:
            account = account_or_id
            account_id = account.id

        logger = logging.getLogger(__name__)
        logger.info(f"Starting history catch-up for account {account_id}")

        try:
            # Получаем диалоги (последние 100, чтобы покрыть большинство активных переписок)
            logger.debug(f"Fetching dialogs for account {account_id}...")
            dialogs = await client.get_dialogs(limit=100)
            logger.info(f"Found {len(dialogs)} dialogs for account {account_id}")
            
            for dialog in dialogs:
                # Личные диалоги и группы; broadcast-каналы исключены.
                if not (dialog.is_user or dialog.is_group):
                    continue
                    
                try:
                    chat_entity = dialog.entity
                    chat_id = chat_entity.id
                    peer_metadata = self._telegram_peer_metadata(chat_entity)
                    safe_title = dialog.title.encode('ascii', 'replace').decode('ascii') if dialog.title else "Unknown"
                    logger.debug(f"Processing dialog: {safe_title} (ID: {chat_id})")
                    
                    # Находим или создаем чат в БД
                    @database_sync_to_async
                    def get_or_create_chat():
                        try:
                            username = getattr(chat_entity, 'username', None)
                            chat_obj, created = Chat.objects.get_or_create(
                                telegram_id=chat_id,
                                telegram_account=account,
                                defaults={
                                    'chat_type': 'private' if dialog.is_user else 'group' if dialog.is_group else 'channel',
                                    'title': dialog.title or "Unknown",
                                    'username': username,
                                    'is_bot': bool(dialog.is_user and getattr(chat_entity, 'bot', False)),
                                    'metadata': peer_metadata,
                                }
                            )
                            # Update title and peer kind if changed.
                            update_fields = []
                            if not created and dialog.title and chat_obj.title != dialog.title:
                                chat_obj.title = dialog.title
                                update_fields.append('title')
                            peer_is_bot = bool(dialog.is_user and getattr(chat_entity, 'bot', False))
                            if chat_obj.is_bot != peer_is_bot:
                                chat_obj.is_bot = peer_is_bot
                                update_fields.append('is_bot')
                            if peer_metadata and not (chat_obj.metadata or {}).get('telegram_peer'):
                                chat_obj.metadata = {**(chat_obj.metadata or {}), **peer_metadata}
                                update_fields.append('metadata')
                            if update_fields:
                                chat_obj.save(update_fields=[*update_fields, 'updated_at'])
                                
                            last_msg = MessageModel.objects.filter(chat=chat_obj).order_by('-telegram_date').first()
                            last_msg_date = last_msg.telegram_date if last_msg else None
                            return chat_obj, last_msg_date
                        except Exception as e:
                            logger.error(f"Error getting/creating chat {chat_id}: {e}")
                            return None, None

                    chat_obj, last_db_date = await get_or_create_chat()
                    
                    if not chat_obj:
                        continue

                    # Optimization: Get last message ID from DB
                    @database_sync_to_async
                    def get_last_db_msg_id():
                        last_m = MessageModel.objects.filter(chat=chat_obj).order_by('-telegram_id').first()
                        return last_m.telegram_id if last_m else None
                    
                    last_db_id = await get_last_db_msg_id()
                    
                    # If dialog.message.id matches what we have, nothing new here
                    if not force and dialog.message and last_db_id == dialog.message.id:
                        # logger.debug(f"Chat {chat_id} is up to date (last ID {last_db_id}), skipping messages fetch.")
                        continue
                    
                    # Fetch only a small overlap for dialogs that are actually
                    # behind. Live messages arrive through Telethon events.
                    logger.debug(f"Fetching last 20 messages for chat {chat_id} (reason: last_db_id={last_db_id} vs tg_id={dialog.message.id if dialog.message else 'None'})...")
                    history = await client.get_messages(chat_entity, limit=20)
                    
                    logger.debug(f"Telethon returned {len(history)} messages for chat {chat_id}")
                    
                    new_messages_count = 0
                    for msg in history:
                        if not msg.message and not msg.media and not getattr(msg, 'action', None):
                            continue

                        @database_sync_to_async
                        def save_msg(message_data):
                            from django.db import IntegrityError
                            from .reactions import telegram_reaction_summary
                            try:
                                # Quick check if exists
                                if MessageModel.objects.filter(telegram_id=message_data.id, chat=chat_obj).exists():
                                    return None
                                    
                                # Convert date
                                msg_date = message_data.date
                                if msg_date and not msg_date.tzinfo:
                                    msg_date = timezone.make_aware(msg_date)
                                
                                # Skip if older than last_db_date
                                if last_db_date and msg_date < last_db_date.replace(microsecond=0):
                                    return None

                                # Use system method for type determination
                                msg_type = self._get_message_type(message_data) or 'text'
                                
                                logger.debug(f"Eval message {message_data.id}: date={msg_date}, type={msg_type}, out={message_data.out}")

                                # Create and save
                                message_obj = MessageModel.objects.create(
                                    chat=chat_obj,
                                    telegram_id=message_data.id,
                                    text=message_data.message or "",
                                    is_outgoing=message_data.out,
                                    telegram_date=msg_date,
                                    message_type=msg_type,
                                    from_user_id=message_data.sender_id,
                                    from_user_name=getattr(dialog, 'title', 'Unknown'), # Fallback
                                    status=MessageModel.MessageStatus.RECEIVED,
                                    media_caption=getattr(message_data, 'message', None) if msg_type != 'text' else None,
                                    metadata={
                                        'reactions': telegram_reaction_summary(
                                            getattr(message_data, 'reactions', None)
                                        ),
                                        'special_content': telegram_special_content(message_data),
                                        'forward_info': telegram_forward_info(message_data),
                                    },
                                )
                                logger.info(f"Saved NEW message {message_data.id} in chat {chat_id} during sync")
                                return message_obj
                            except Exception as e:
                                logger.error(f"Error saving message {message_data.id}: {e}")
                                return None

                        message_obj = await save_msg(msg)
                        if message_obj:
                            if msg.media and not message_obj.media_file_path:
                                await self._download_media_telethon(client, msg, message_obj)
                            new_messages_count += 1
                            # Optional: Update chat stats and notify WS
                            # (Mirroring handle_message logic for consistency)
                            try:
                                @database_sync_to_async
                                def update_stats():
                                    chat_obj.message_count += 1
                                    chat_obj.last_message_at = message_obj.telegram_date
                                    if not message_obj.is_outgoing:
                                        chat_obj.unread_count += 1
                                    chat_obj.save(update_fields=['message_count', 'last_message_at', 'unread_count'])
                                await update_stats()
                            except: pass

                    if new_messages_count > 0:
                        safe_title = chat_obj.title.encode('ascii', 'replace').decode('ascii') if chat_obj.title else "Unknown"
                        logger.info(f"Synced {new_messages_count} missed messages for chat {safe_title}")
                    else:
                        logger.debug(f"No new messages for chat {chat_id}")
                        
                except Exception as e:
                    safe_title = getattr(dialog, 'title', 'Unknown').encode('ascii', 'replace').decode('ascii')
                    logger.error(f"Error syncing chat {safe_title}: {e}")
                    continue
                    
            logger.info(f"History catch-up completed for account {account.id}")
            
        except Exception as e:
            logger.exception(f"Global error in history catch-up: {e}")

    def terminate_session_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for terminating session"""
        loop = self._ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(self.terminate_session(account), loop)
        return future.result()

    async def terminate_session(self, account: TelegramAccount) -> dict:
        """
        Forcefully log out of both active and pending sessions and clear DB fields
        """
        # 1. Stop active client
        if account.id in self._clients:
            await self.stop_client(account.id, mark_inactive=False)
        
        # 2. Logout of current session if it exists
        if account.session_string:
            try:
                client = self._create_client(StringSession(account.session_string), account.api_id, account.api_hash)
                await client.connect()
                await client.log_out()
                await client.disconnect()
            except:
                pass
            account.session_string = None

        # 3. Logout of pending session if it exists
        if account.pending_session_string:
            try:
                client = self._create_client(StringSession(account.pending_session_string), account.api_id, account.api_hash)
                await client.connect()
                await client.log_out()
                await client.disconnect()
            except:
                pass
            account.pending_session_string = None

        # 4. Clear all pending fields and set status to INACTIVE
        account.pending_session_name = None
        account.pending_phone_code_hash = None
        account.pending_code_sent_at = None
        account.pending_code_type = None
        account.status = TelegramAccount.AccountStatus.INACTIVE
        account.last_error = "Сессия аннулирована вручную (выход выполнен)"
        await database_sync_to_async(account.save)()

        return {'success': True}
    def sync_all_active_sync(self):
        """Sync wrapper for sync_all_active"""
        loop = self._ensure_background_loop()
        asyncio.run_coroutine_threadsafe(self.sync_all_active(), loop)

    async def sync_all_active(self):
        """Trigger sync for all active and running accounts"""
        from ..models import TelegramAccount
        from asgiref.sync import sync_to_async
        
        # Get all accounts that SHOULD be running
        @sync_to_async
        def get_active_accounts():
            return list(TelegramAccount.objects.filter(
                account_type=TelegramAccount.AccountType.PERSONAL,
                status=TelegramAccount.AccountStatus.ACTIVE
            ))
            
        active_accounts = await get_active_accounts()
        logger.info(f"Sync starting for {len(active_accounts)} active accounts")
        
        for account in active_accounts:
            client = self._clients.get(account.id)
            if not client or not client.is_connected():
                logger.warning(f"Client for account {account.id} is NOT running. Attempting to restart...")
                # Try to restart client in this process
                await self.start_client(account)
                client = self._clients.get(account.id)
                
            if client and client.is_connected():
                logger.debug(f"Triggering sync for account {account.id}")
                await self.sync_messages_for_account_id(client, account.id)
            else:
                logger.error(f"Failed to start/find active client for account {account.id}")

    async def sync_messages_for_account_id(self, client: TelegramClient, account_id: int, force: bool = False):
        """Wrapper for sync_messages_for_account using ID"""
        from asgiref.sync import sync_to_async
        account = await sync_to_async(TelegramAccount.objects.get)(id=account_id)
        return await self.sync_messages_for_account(client, account, force)

    async def sync_messages_for_account(self, client: TelegramClient, account: TelegramAccount, force: bool = False):
        """
        On-demand synchronization for a specific account with throttling.
        
        Args:
            client: Active Telethon client
            account: TelegramAccount model
            force: Skip 30s throttling if True
        """
        now = time.time()
        last_sync = self._last_sync_time.get(account.id, 0)
        
        # No throttling as requested by USER

        # If a catchup is already running, wait for it instead of starting a new one
        if account.id in self._catchup_tasks:
            logger.info(f"Sync for account {account.id} already in progress, waiting...")
            try:
                await self._catchup_tasks[account.id]
                return True
            except Exception as e:
                logger.error(f"Waiting for existing sync failed for {account.id}: {e}")
                return False

        logger.info(f"Triggering on-demand sync for account {account.id}")
        self._last_sync_time[account.id] = now
        
        try:
            self._catchup_tasks[account.id] = asyncio.create_task(self._catch_up_history(client, account, force=force))
            await self._catchup_tasks[account.id]
            return True
        except Exception as e:
            logger.error(f"On-demand sync failed for account {account.id}: {e}")
            return False
        finally:
            self._catchup_tasks.pop(account.id, None)

    def start_all_active_sync(self):
        """Sync wrapper for start_all_active"""
        return self.run_async_sync(self.start_all_active())

    async def start_all_active(self):
        """Reconcile desired database state with clients owned by this connector."""
        from ..models import TelegramAccount

        @database_sync_to_async
        def get_active_accounts():
            return list(TelegramAccount.objects.filter(
                account_type=TelegramAccount.AccountType.PERSONAL,
                status=TelegramAccount.AccountStatus.ACTIVE,
            ))

        active_accounts = await get_active_accounts()
        active_ids = {account.id for account in active_accounts}
        logger.debug(f"Reconciling {len(active_accounts)} active accounts")

        # Stop clients disabled from Admin. Keep the database status unchanged.
        for account_id in set(self._clients) - active_ids:
            await self.stop_client(account_id, mark_inactive=False)

        for account in active_accounts:
            try:
                if account.restart_requested_at:
                    if account.id in self._clients:
                        await self.stop_client(account.id, mark_inactive=False)
                    await database_sync_to_async(
                        TelegramAccount.objects.filter(pk=account.id).update
                    )(restart_requested_at=None)
                    account.restart_requested_at = None

                if account.id not in self._clients:
                    await self.start_client(account)
            except Exception as e:
                logger.error(f"Failed to reconcile account {account.id}: {e}")
