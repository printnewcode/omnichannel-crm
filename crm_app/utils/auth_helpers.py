"""
Утилиты для авторизации Telethon аккаунтов
"""
import asyncio
import logging
from typing import Optional, Tuple
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
)

logger = logging.getLogger(__name__)


class TelethonAuthHelper:
    """
    Помощник для авторизации Telethon аккаунтов
    Обрабатывает процесс получения OTP и создания сессии
    """
    
    @staticmethod
    async def send_code(
        api_id: int,
        api_hash: str,
        phone_number: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Отправить код подтверждения на телефон
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            phone_number: Номер телефона (с кодом страны, например +1234567890)
            
        Returns:
            Tuple[bool, Optional[str], Optional[str]]: 
                (success, phone_code_hash, error_message)
        """
        try:
            # Создание временного клиента
            client = TelegramClient(StringSession(), api_id, api_hash)
            
            await client.connect()
            
            # Отправка кода
            sent_code = await client.send_code_request(phone_number)
            phone_code_hash = sent_code.phone_code_hash
            
            await client.disconnect()
            
            logger.info(f"Code sent to {phone_number}")
            return True, phone_code_hash, None
            
        except FloodWaitError as e:
            error_msg = f"FloodWait: {e.seconds} seconds"
            logger.warning(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Error sending code: {str(e)}"
            logger.exception(error_msg)
            return False, None, error_msg
    
    @staticmethod
    async def verify_code(
        api_id: int,
        api_hash: str,
        phone_number: str,
        phone_code_hash: str,
        otp_code: str,
        password: Optional[str] = None,
        session_string: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Подтвердить код и получить session string
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            phone_number: Номер телефона
            phone_code_hash: Хэш кода из send_code
            otp_code: Код подтверждения
            password: Пароль для 2FA (если требуется)
            
        Returns:
            Tuple[bool, Optional[str], Optional[str]]:
                (success, session_string, error_message)
        """
        try:
            # Создание временного клиента
            if not session_string:
                return False, None, "Session string is required for OTP verification"

            client = TelegramClient(StringSession(session_string), api_id, api_hash)
            
            await client.connect()
            
            try:
                # Подтверждение кода
                signed_in = await client.sign_in(
                    phone=phone_number,
                    code=otp_code,
                    phone_code_hash=phone_code_hash
                )
            except SessionPasswordNeededError:
                # Требуется пароль для 2FA
                if not password:
                    await client.disconnect()
                    return False, None, "Password required for 2FA"
                
                # Проверка пароля
                signed_in = await client.sign_in(password=password)
            
            # Получение session string
            session_string = client.session.save()
            
            await client.disconnect()
            
            logger.info(f"Successfully authenticated {phone_number}")
            return True, session_string, None
            
        except PhoneCodeInvalidError:
            error_msg = "Invalid OTP code"
            logger.warning(error_msg)
            return False, None, error_msg
        except PhoneCodeExpiredError:
            error_msg = "OTP code expired"
            logger.warning(error_msg)
            return False, None, error_msg
        except FloodWaitError as e:
            error_msg = f"FloodWait: {e.seconds} seconds"
            logger.warning(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Error verifying code: {str(e)}"
            logger.exception(error_msg)
            return False, None, error_msg
    
    @staticmethod
    async def get_account_info(
        api_id: int,
        api_hash: str,
        session_string: str
    ) -> Tuple[bool, Optional[dict], Optional[str]]:
        """
        Получить информацию об аккаунте
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            session_string: Session string аккаунта
            
        Returns:
            Tuple[bool, Optional[dict], Optional[str]]:
                (success, account_info, error_message)
        """
        try:
            client = TelegramClient(StringSession(session_string), api_id, api_hash)
            await client.connect()
            me = await client.get_me()
            await client.disconnect()
            
            account_info = {
                'id': me.id,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone_number': me.phone_number,
            }
            
            return True, account_info, None
            
        except AuthKeyUnregisteredError:
            error_msg = "Session expired or invalid"
            logger.warning(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Error getting account info: {str(e)}"
            logger.exception(error_msg)
            return False, None, error_msg


# Backwards compatibility alias
HydrogramAuthHelper = TelethonAuthHelper
