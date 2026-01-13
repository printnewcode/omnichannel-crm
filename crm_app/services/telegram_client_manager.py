"""
Менеджер для управления множественными Hydrogram клиентами
Обрабатывает динамическое создание, запуск и остановку клиентов
"""
import asyncio
import logging
from typing import Dict, Optional, List
from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async
from hydrogram import Client
from hydrogram.errors import FloodWait, AuthKeyUnregistered, UserDeactivated
from ..models import TelegramAccount

logger = logging.getLogger(__name__)


class TelegramClientManager:
    """
    Singleton менеджер для управления несколькими Hydrogram клиентами
    Работает в асинхронном режиме внутри Django
    """
    
    _instance: Optional['TelegramClientManager'] = None
    _clients: Dict[int, Client] = {}
    _tasks: Dict[int, asyncio.Task] = {}
    _loop: Optional[asyncio.AbstractEventLoop] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._lock = asyncio.Lock()
    
    async def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """Получить или создать event loop"""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def start_client_sync(self, account: TelegramAccount) -> bool:
        """Sync wrapper for start_client"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.start_client(account))
        finally:
            loop.close()

    async def start_client(self, account: TelegramAccount) -> bool:
        """
        Запустить Hydrogram клиент для аккаунта

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
            await sync_to_async(account.save)()
            return False

        try:
            # Создание клиента Hydrogram
            client = Client(
                name=f"account_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                session_string=account.session_string if account.session_string else None,
                workdir=str(settings.BASE_DIR / 'sessions'),
            )

            # Запуск клиента
            await client.start()

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

            # Сохранение session string если его не было
            if not account.session_string:
                account.session_string = await client.export_session_string()

            await sync_to_async(account.save)()

            # Регистрация обработчиков
            client.add_handler(self._create_message_handler(account))

            # Сохранение клиента и запуск задачи прослушивания
            self._clients[account.id] = client

            # Запуск задачи для обработки обновлений
            loop = await self._get_or_create_loop()
            task = loop.create_task(self._listen_updates(client, account))
            self._tasks[account.id] = task

            logger.info(f"Successfully started client for account {account.id}")
            return True

        except AuthKeyUnregistered:
            logger.error(f"Auth key unregistered for account {account.id}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = "Сессия недействительна. Требуется повторная авторизация"
            account.session_string = None  # Сброс сессии
            await sync_to_async(account.save)()
            return False
        except UserDeactivated:
            logger.error(f"User deactivated for account {account.id}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = "Аккаунт деактивирован"
            await sync_to_async(account.save)()
            return False
        except FloodWait as e:
            logger.warning(f"FloodWait for account {account.id}: {e.value} seconds")
            account.last_error = f"FloodWait: {e.value} секунд"
            await sync_to_async(account.save)()
            return False
        except Exception as e:
            logger.exception(f"Error starting client for account {account.id}: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            account.error_count += 1
            await sync_to_async(account.save)()
            return False
    
    def stop_client_sync(self, account_id: int) -> bool:
        """Sync wrapper for stop_client"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.stop_client(account_id))
        finally:
            loop.close()

    async def stop_client(self, account_id: int) -> bool:
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
            await client.stop()
            await client.disconnect()
            
            del self._clients[account_id]
            
            # Обновление статуса в БД
            try:
                account = TelegramAccount.objects.get(id=account_id)
                account.status = TelegramAccount.AccountStatus.INACTIVE
                account.save()
            except TelegramAccount.DoesNotExist:
                pass
            
            logger.info(f"Successfully stopped client for account {account_id}")
            return True
            
        except Exception as e:
            logger.exception(f"Error stopping client for account {account_id}: {e}")
            return False
    
    async def send_message(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        """
        Отправить сообщение через Hydrogram клиент
        
        Args:
            account_id: ID аккаунта
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
            
        Returns:
            int: Message ID если успешно, None если ошибка
        """
        if account_id not in self._clients:
            logger.error(f"Client for account {account_id} is not running")
            return None
        
        try:
            client = self._clients[account_id]
            sent_message = await client.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id
            )
            
            # Обновление последней активности
            try:
                account = TelegramAccount.objects.get(id=account_id)
                account.last_activity = timezone.now()
                account.save(update_fields=['last_activity'])
            except TelegramAccount.DoesNotExist:
                pass
            
            return sent_message.id
            
        except FloodWait as e:
            logger.warning(f"FloodWait when sending message: {e.value} seconds")
            # Можно добавить retry с задержкой
            await asyncio.sleep(e.value)
            return await self.send_message(account_id, chat_id, text, reply_to_message_id)
        except Exception as e:
            logger.exception(f"Error sending message: {e}")
            return None
    
    def _create_message_handler(self, account: TelegramAccount):
        """Создать обработчик сообщений для аккаунта"""
        from hydrogram import filters
        from hydrogram.handlers import MessageHandler
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        async def handle_message(client: Client, message):
            """Обработка входящих сообщений"""
            from ..models import Chat, Message as MessageModel
            from ..tasks import process_incoming_message
            from channels.db import database_sync_to_async

            try:
                # Получение или создание чата
                @database_sync_to_async
                def get_or_create_chat():
                    chat, created = Chat.objects.get_or_create(
                        telegram_id=message.chat.id,
                        telegram_account=account,
                        defaults={
                            'chat_type': message.chat.type.name.lower(),
                            'title': getattr(message.chat, 'title', None),
                            'username': getattr(message.chat, 'username', None),
                            'first_name': getattr(message.chat, 'first_name', None),
                            'last_name': getattr(message.chat, 'last_name', None),
                            'metadata': {},
                        }
                    )

                    # Обновление информации о чате
                    updated = False
                    if hasattr(message.chat, 'title') and message.chat.title != chat.title:
                        chat.title = message.chat.title
                        updated = True
                    if hasattr(message.chat, 'username') and message.chat.username != chat.username:
                        chat.username = message.chat.username
                        updated = True

                    if updated or created:
                        chat.save()

                    return chat, created

                chat, chat_created = await get_or_create_chat()

                # Определение типа сообщения и медиа
                message_type = self._get_message_type(message)
                media_file_id = self._get_media_file_id(message)

                # Создание записи сообщения в БД
                @database_sync_to_async
                def create_message_record():
                    # Поиск сообщения на которое отвечают
                    reply_to_message = None
                    if message.reply_to_message_id:
                        try:
                            reply_to_message = MessageModel.objects.get(
                                telegram_id=message.reply_to_message_id,
                                chat=chat
                            )
                        except MessageModel.DoesNotExist:
                            pass

                    # Создание сообщения
                    message_obj = MessageModel.objects.create(
                        telegram_id=message.id,
                        chat=chat,
                        text=message.text or message.caption or None,
                        message_type=message_type,
                        status=MessageModel.MessageStatus.RECEIVED,
                        from_user_id=message.from_user.id if message.from_user else None,
                        from_user_name=message.from_user.first_name if message.from_user else None,
                        from_user_username=message.from_user.username if message.from_user else None,
                        is_outgoing=message.outgoing,
                        telegram_date=message.date,
                        reply_to_message=reply_to_message,
                        media_file_id=media_file_id,
                        media_caption=message.caption,
                        metadata={}
                    )

                    # Обновление статистики чата
                    chat.message_count += 1
                    chat.last_message_at = message.date
                    if not message.outgoing:
                        chat.unread_count += 1
                    chat.save(update_fields=['message_count', 'last_message_at', 'unread_count'])

                    return message_obj

                message_obj = await create_message_record()

                # Обработка медиа если есть
                if media_file_id and message_type in ['photo', 'video', 'voice', 'document']:
                    # Скачивание медиа через Hydrogram
                    try:
                        await self._download_media_hydrogram(client, message, message_obj)
                    except Exception as e:
                        logger.exception(f"Error downloading media via Hydrogram: {e}")

                # Отправка real-time обновления через WebSocket
                try:
                    channel_layer = get_channel_layer()

                    # Получение операторов этого чата
                    @database_sync_to_async
                    def get_assigned_operators():
                        from ..models import ChatAssignment
                        assignments = ChatAssignment.objects.filter(
                            chat=chat,
                            is_active=True
                        ).select_related('operator__user')
                        return [assignment.operator.user.id for assignment in assignments]

                    operator_ids = await get_assigned_operators()

                    # Отправка обновления каждому оператору
                    for operator_id in operator_ids:
                        async_to_sync(channel_layer.group_send)(
                            f"operator_{operator_id}",
                            {
                                'type': 'new_message',
                                'message': {
                                    'id': message_obj.id,
                                    'telegram_id': message_obj.telegram_id,
                                    'chat_id': message_obj.chat.id,
                                    'text': message_obj.text,
                                    'message_type': message_obj.message_type,
                                    'status': message_obj.status,
                                    'is_outgoing': message_obj.is_outgoing,
                                    'from_user_name': message_obj.from_user_name,
                                    'from_user_username': message_obj.from_user_username,
                                    'telegram_date': message_obj.telegram_date.isoformat(),
                                    'media_file_path': message_obj.media_file_path,
                                    'media_caption': message_obj.media_caption,
                                    'reply_to_message_id': message_obj.reply_to_message_id,
                                }
                            }
                        )

                        # Отправка обновления чата
                        async_to_sync(channel_layer.group_send)(
                            f"operator_{operator_id}",
                            {
                                'type': 'chat_updated',
                                'chat': {
                                    'id': chat.id,
                                    'telegram_id': chat.telegram_id,
                                    'title': chat.title or chat.first_name or chat.username or f"Chat {chat.telegram_id}",
                                    'chat_type': chat.chat_type,
                                    'unread_count': chat.unread_count,
                                    'last_message_at': chat.last_message_at.isoformat() if chat.last_message_at else None,
                                    'message_count': chat.message_count,
                                }
                            }
                        )

                except Exception as e:
                    logger.exception(f"Error sending WebSocket update: {e}")

                logger.info(f"Processed incoming message {message.id} for chat {chat.id}")

            except Exception as e:
                logger.exception(f"Error handling message: {e}")

        return MessageHandler(handle_message, filters.incoming & ~filters.outgoing)
    
    def _get_message_type(self, message) -> str:
        """Определить тип сообщения"""
        if message.photo:
            return 'photo'
        elif message.video:
            return 'video'
        elif message.voice:
            return 'voice'
        elif message.document:
            return 'document'
        elif message.sticker:
            return 'sticker'
        elif message.location:
            return 'location'
        elif message.contact:
            return 'contact'
        else:
            return 'text'
    
    def _get_media_file_id(self, message) -> Optional[str]:
        """Получить file_id медиа"""
        if message.photo:
            return message.photo.file_id
        elif message.video:
            return message.video.file_id
        elif message.voice:
            return message.voice.file_id
        elif message.document:
            return message.document.file_id
        return None

    async def _download_media_hydrogram(self, client: Client, message, message_obj):
        """Скачать медиа через Hydrogram клиент"""
        import os
        from django.conf import settings
        from ..models import Message as MessageModel

        try:
            # Создание директории для медиа
            media_dir = settings.BASE_DIR / 'media' / 'telegram' / message_obj.message_type
            os.makedirs(media_dir, exist_ok=True)

            # Определение имени файла
            file_ext = self._get_file_extension(message, message_obj.message_type)
            file_name = f"{message_obj.id}_{message.id}_{message.date.timestamp()}{file_ext}"
            local_path = media_dir / file_name

            # Скачивание файла
            await client.download_media(message, file_name=str(local_path))

            # Сохранение пути в БД
            relative_path = f"telegram/{message_obj.message_type}/{file_name}"
            message_obj.media_file_path = relative_path
            message_obj.save(update_fields=['media_file_path'])

            logger.info(f"Downloaded media for message {message_obj.id}: {local_path}")

        except Exception as e:
            logger.exception(f"Error downloading media via Hydrogram: {e}")

    def _get_file_extension(self, message, message_type: str) -> str:
        """Получить расширение файла"""
        if message_type == 'photo':
            return '.jpg'
        elif message_type == 'video':
            return '.mp4'
        elif message_type == 'voice':
            return '.ogg'
        elif message_type == 'document':
            if hasattr(message.document, 'file_name') and message.document.file_name:
                _, ext = os.path.splitext(message.document.file_name)
                return ext or '.bin'
            return '.bin'
        return '.bin'
    
    async def _listen_updates(self, client: Client, account: TelegramAccount):
        """Задача для прослушивания обновлений"""
        try:
            await client.start()
            logger.info(f"Listening for updates on account {account.id}")
            # Клиент будет прослушивать обновления автоматически
            # Эта задача просто держит соединение активным
            while True:
                await asyncio.sleep(60)  # Проверка каждую минуту
                if not client.is_connected:
                    logger.warning(f"Client {account.id} disconnected, reconnecting...")
                    await client.connect()
        except asyncio.CancelledError:
            logger.info(f"Stopped listening for account {account.id}")
            raise
        except Exception as e:
            logger.exception(f"Error in listen_updates for account {account.id}: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            account.save()
    
    def authenticate_account_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for authenticate_account"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.authenticate_account(account))
        finally:
            loop.close()

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

        if account.id in self._clients:
            await self.stop_client(account.id)

        try:
            # Создание клиента для авторизации с proper async context management
            client = Client(
                name=f"auth_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                session_string=None,  # Новая сессия
                workdir=str(settings.BASE_DIR / 'sessions'),
            )

            # Запуск процесса авторизации
            account.status = TelegramAccount.AccountStatus.AUTHENTICATING
            account.last_error = None
            await sync_to_async(account.save)()

            # Попытка авторизации - manual connection to avoid interactive prompts
            await client.connect()

            try:
                if not client.me:
                    try:
                        # Отправка кода на номер телефона
                        sent_code = await client.send_code(account.phone_number)

                        # Сохраняем phone_code_hash для верификации
                        import time
                        account.session_string = f"phone_code_hash:{sent_code.phone_code_hash}:{int(time.time())}"
                        await sync_to_async(account.save)()

                        # Определяем тип отправленного кода
                        code_type = sent_code.type.name if hasattr(sent_code.type, 'name') else str(sent_code.type)

                        # Формируем сообщение в зависимости от типа кода
                        if code_type == 'SMS':
                            message = 'OTP код отправлен по SMS на ваш номер телефона'
                            if sent_code.next_type:
                                next_type_name = sent_code.next_type.name if hasattr(sent_code.next_type, 'name') else str(sent_code.next_type)
                                message += f'. Если SMS не пришел, следующий метод: {next_type_name}'
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
                            'next_type': sent_code.next_type.name if sent_code.next_type and hasattr(sent_code.next_type, 'name') else None,
                            'timeout': sent_code.timeout
                        }

                    except Exception as e:
                        logger.exception(f"Error sending code: {e}")

                        # Handle FloodWait errors specifically
                        error_message = str(e)
                        if "FLOOD_WAIT" in error_message:
                            # Extract wait time from error message
                            import re
                            wait_match = re.search(r'wait of (\d+) seconds', error_message)
                            if wait_match:
                                wait_seconds = int(wait_match.group(1))
                                wait_hours = wait_seconds // 3600
                                wait_minutes = (wait_seconds % 3600) // 60

                                if wait_hours > 0:
                                    readable_wait = f"{wait_hours} часов {wait_minutes} минут"
                                else:
                                    readable_wait = f"{wait_minutes} минут"

                                user_message = f"Превышен лимит запросов Telegram. Подождите {readable_wait} перед следующей попыткой."
                            else:
                                user_message = "Превышен лимит запросов Telegram. Попробуйте позже."
                        else:
                            user_message = f"Ошибка отправки кода: {error_message}"

                        account.status = TelegramAccount.AccountStatus.ERROR
                        account.last_error = user_message
                        await sync_to_async(account.save)()
                        await client.disconnect()
                        return {
                            'success': False,
                            'error': user_message
                        }
                else:
                    # Уже авторизован
                    session_string = await client.export_session_string()
                    account.session_string = session_string
                    account.status = TelegramAccount.AccountStatus.ACTIVE

                    # Получение информации о пользователе
                    me = await client.get_me()
                    account.telegram_user_id = me.id
                    account.first_name = me.first_name
                    account.last_name = me.last_name
                    account.username = me.username
                    await sync_to_async(account.save)()

                    await client.disconnect()
                    return {
                        'success': True,
                        'status': 'authenticated',
                        'message': 'Account already authenticated'
                    }

            except Exception as e:
                logger.exception(f"Error in authenticate_account: {e}")
                await client.disconnect()
                account.status = TelegramAccount.AccountStatus.ERROR
                account.last_error = str(e)
                await sync_to_async(account.save)()
                return {
                    'success': False,
                    'error': f"Authentication failed: {str(e)}"
                }
            finally:
                try:
                    await client.disconnect()
                except:
                    pass  # Already disconnected or connection error

        except Exception as e:
            logger.exception(f"Error in authentication: {e}")
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            await sync_to_async(account.save)()
            return {
                'success': False,
                'error': str(e)
            }

    def verify_otp_sync(self, account: TelegramAccount, otp_code: str, password: str = None) -> dict:
        """Sync wrapper for verify_otp"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.verify_otp(account, otp_code, password))
        finally:
            loop.close()

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

        # Извлечение phone_code_hash из session_string
        if not account.session_string or not account.session_string.startswith('phone_code_hash:'):
            return {
                'success': False,
                'error': 'Аутентификация не запущена. Сначала нажмите "Начать аутентификацию".'
            }

        # Парсим phone_code_hash и timestamp
        parts = account.session_string.split(':')
        if len(parts) != 3:
            return {
                'success': False,
                'error': 'Некорректные данные аутентификации. Запустите аутентификацию заново.'
            }

        phone_code_hash = parts[1]
        timestamp = int(parts[2])
        import time
        current_time = int(time.time())

        # Проверяем не истек ли хеш (более 5 минут)
        if current_time - timestamp > 300:  # 5 minutes
            logger.warning(f"Phone code hash expired for account {account.id}, restarting authentication")
            try:
                # Автоматически перезапускаем аутентификацию
                restart_result = await self.authenticate_account(account)
                if restart_result['success'] and restart_result.get('status') == 'otp_required':
                    return {
                        'success': False,
                        'error': f'Код истек. Автоматически запущена новая аутентификация. Проверьте Telegram для нового кода ({restart_result.get("code_type", "SMS")}).'
                    }
                else:
                    return {
                        'success': False,
                        'error': f'Код истек. Не удалось автоматически перезапустить аутентификацию: {restart_result.get("error", "Неизвестная ошибка")}'
                    }
            except Exception as restart_error:
                logger.exception(f"Error restarting authentication for account {account.id}: {restart_error}")
                return {
                    'success': False,
                    'error': 'Код подтверждения истек. Запустите аутентификацию заново вручную.'
                }

        try:
            # Создание клиента для верификации с proper async context management
            logger.info(f"Creating verification client for account {account.id}")
            client = Client(
                name=f"auth_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                session_string=None,
                workdir=str(settings.BASE_DIR / 'sessions'),
            )

            # Manual connection to avoid interactive prompts
            await client.connect()

            try:
                logger.info(f"Client connected successfully for account {account.id} verification")
                # Верификация с сохраненным phone_code_hash
                logger.info(f"Attempting sign-in with stored hash for account {account.id}")
                await client.sign_in(
                    phone_number=account.phone_number,
                    phone_code_hash=phone_code_hash,
                    phone_code=otp_code
                )
                logger.info(f"Sign-in successful for account {account.id}")
            except Exception as sign_in_error:
                    # Handle 2FA password requirement
                    if "password" in str(sign_in_error).lower() and password:
                        try:
                            await client.check_password(password)
                        except Exception as pwd_error:
                            account.status = TelegramAccount.AccountStatus.ERROR
                            account.last_error = f"Invalid 2FA password: {str(pwd_error)}"
                            await sync_to_async(account.save)()
                            return {
                                'success': False,
                                'error': f"2FA password error: {str(pwd_error)}"
                            }
                    else:
                        # Handle other sign-in errors
                        error_message = str(sign_in_error)
                        if "EOF when reading a line" in error_message:
                            error_message = "Ошибка сети при подключении к Telegram. Проверьте интернет-соединение и попробуйте еще раз."
                        elif "PHONE_CODE_INVALID" in error_message:
                            error_message = "Неверный код подтверждения. Проверьте код и попробуйте снова."
                        elif "PHONE_CODE_EMPTY" in error_message:
                            error_message = "Код подтверждения не указан."
                        elif "PHONE_CODE_EXPIRED" in error_message:
                            # Если хеш истек при попытке входа, перезапускаем аутентификацию
                            logger.warning(f"Phone code hash expired during sign-in for account {account.id}, restarting authentication")
                            try:
                                restart_result = await self.authenticate_account(account)
                                if restart_result['success'] and restart_result.get('status') == 'otp_required':
                                    error_message = f'Код истек. Автоматически запущена новая аутентификация. Проверьте Telegram для нового кода ({restart_result.get("code_type", "SMS")}).'
                                else:
                                    error_message = f'Код истек. Не удалось автоматически перезапустить аутентификацию.'
                            except Exception as restart_error:
                                logger.exception(f"Error restarting authentication for account {account.id}: {restart_error}")
                                error_message = "Код подтверждения истек. Запустите аутентификацию заново."
                        elif "connection" in error_message.lower() or "network" in error_message.lower():
                            error_message = "Проблема с подключением к Telegram. Проверьте интернет-соединение."

                        account.status = TelegramAccount.AccountStatus.ERROR
                        account.last_error = f"OTP verification failed: {error_message}"
                        await sync_to_async(account.save)()
                        await client.disconnect()
                        return {
                            'success': False,
                            'error': error_message
                        }

            # Успешная авторизация
            session_string = await client.export_session_string()

            # Получение информации о пользователе
            me = await client.get_me()
            account.telegram_user_id = me.id
            account.first_name = me.first_name
            account.last_name = me.last_name
            account.username = me.username
            account.session_string = session_string
            account.status = TelegramAccount.AccountStatus.ACTIVE
            account.last_error = None
            account.error_count = 0
            await sync_to_async(account.save)()

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
            except:
                pass
            account.status = TelegramAccount.AccountStatus.ERROR
            account.last_error = str(e)
            await sync_to_async(account.save)()
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
            # Create client for sending verification code
            client = Client(
                name=f"verify_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                session_string=None,
                workdir=str(settings.BASE_DIR / 'sessions'),
            )

            # Manual connection to avoid interactive prompts
            await client.connect()

            try:
                # Send new code
                sent_code = await client.send_code(account.phone_number)

                # Update session string with new hash and timestamp
                import time
                account.session_string = f"phone_code_hash:{sent_code.phone_code_hash}:{int(time.time())}"
                await sync_to_async(account.save)()

                # Determine code type
                code_type = sent_code.type.name if hasattr(sent_code.type, 'name') else str(sent_code.type)

                await client.disconnect()
                return {
                    'success': True,
                    'message': f'OTP код отправлен через {code_type}',
                    'code_type': code_type
                }

            except Exception as e:
                await client.disconnect()
                raise e

        except Exception as e:
            logger.exception(f"Error sending verification code: {e}")

            # Handle FloodWait errors specifically
            error_message = str(e)
            if "FLOOD_WAIT" in error_message:
                # Extract wait time from error message
                import re
                wait_match = re.search(r'wait of (\d+) seconds', error_message)
                if wait_match:
                    wait_seconds = int(wait_match.group(1))
                    wait_hours = wait_seconds // 3600
                    wait_minutes = (wait_seconds % 3600) // 60

                    if wait_hours > 0:
                        readable_wait = f"{wait_hours} часов {wait_minutes} минут"
                    else:
                        readable_wait = f"{wait_minutes} минут"

                    user_message = f"Превышен лимит запросов Telegram. Подождите {readable_wait} перед следующей попыткой."
                else:
                    user_message = "Превышен лимит запросов Telegram. Попробуйте позже."
            else:
                user_message = f"Не удалось отправить код верификации: {error_message}"

            return {
                'success': False,
                'error': user_message
            }

    def send_verification_code_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for send_verification_code"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.send_verification_code(account))
        finally:
            loop.close()

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
        if not account.session_string or not account.session_string.startswith('phone_code_hash:'):
            return {
                'success': False,
                'error': 'No pending authentication found'
            }

        # Parse current phone_code_hash
        parts = account.session_string.split(':')
        current_hash = parts[1] if len(parts) >= 2 else None

        try:
            # Create client for resending
            client = Client(
                name=f"resend_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                session_string=None,
                workdir=str(settings.BASE_DIR / 'sessions'),
            )

            await client.connect()

            try:
                # Send new code (fresh phone_code_hash will be generated)
                sent_code = await client.send_code(account.phone_number)

                # Update session string with new hash and timestamp
                import time
                account.session_string = f"phone_code_hash:{sent_code.phone_code_hash}:{int(time.time())}"
                await sync_to_async(account.save)()

                # Determine code type
                code_type = sent_code.type.name if hasattr(sent_code.type, 'name') else str(sent_code.type)

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

                await client.disconnect()
                return {
                    'success': True,
                    'status': 'otp_resent',
                    'message': message,
                    'phone_code_hash': sent_code.phone_code_hash,
                    'code_type': code_type,
                    'next_type': sent_code.next_type.name if sent_code.next_type and hasattr(sent_code.next_type, 'name') else None,
                    'timeout': sent_code.timeout
                }

            except Exception as e:
                logger.exception(f"Error resending code: {e}")
                await client.disconnect()
                return {
                    'success': False,
                    'error': f"Failed to resend code: {str(e)}"
                }

        except Exception as e:
            logger.exception(f"Error in resend_code: {e}")
            try:
                await client.disconnect()
            except:
                pass
            return {
                'success': False,
                'error': str(e)
            }

    def resend_code_sync(self, account: TelegramAccount) -> dict:
        """Sync wrapper for resend_code"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.resend_code(account))
        finally:
            loop.close()

    def restart_client_sync(self, account_id: int) -> bool:
        """Sync wrapper for restart_client"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.restart_client(account_id))
        finally:
            loop.close()

    async def restart_client(self, account_id: int) -> bool:
        """Перезапустить клиент"""
        await self.stop_client(account_id)
        try:
            account = TelegramAccount.objects.get(id=account_id)
            return await self.start_client(account)
        except TelegramAccount.DoesNotExist:
            return False

    def get_running_accounts(self) -> List[int]:
        """Получить список ID запущенных аккаунтов"""
        return list(self._clients.keys())

    async def start_all_active(self):
        """Запустить все активные аккаунты"""
        accounts = TelegramAccount.objects.filter(
            account_type=TelegramAccount.AccountType.PERSONAL,
            status__in=[
                TelegramAccount.AccountStatus.ACTIVE,
                TelegramAccount.AccountStatus.INACTIVE
            ]
        )

        for account in accounts:
            await self.start_client(account)

    async def stop_all(self):
        """Остановить все клиенты"""
        account_ids = list(self._clients.keys())
        for account_id in account_ids:
            await self.stop_client(account_id)
