"""
Утилиты для авторизации Hydrogram аккаунтов
"""
import asyncio
import logging
from typing import Optional, Tuple
from hydrogram import Client
from hydrogram.session import StringSession
from hydrogram.errors import (
    FloodWait, AuthKeyUnregistered, UserDeactivated,
    PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded
)

logger = logging.getLogger(__name__)


class HydrogramAuthHelper:
    """
    Помощник для авторизации Hydrogram аккаунтов
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
            client = Client(
                name="temp_auth",
                api_id=api_id,
                api_hash=api_hash,
                workdir="/tmp/hydrogram_sessions"
            )
            
            await client.connect()
            
            # Отправка кода
            sent_code = await client.send_code(phone_number)
            phone_code_hash = sent_code.phone_code_hash
            
            await client.disconnect()
            
            logger.info(f"Code sent to {phone_number}")
            return True, phone_code_hash, None
            
        except FloodWait as e:
            error_msg = f"FloodWait: {e.value} seconds"
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
        password: Optional[str] = None
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
            client = Client(
                name="temp_auth",
                api_id=api_id,
                api_hash=api_hash,
                workdir="/tmp/hydrogram_sessions"
            )
            
            await client.connect()
            
            try:
                # Подтверждение кода
                signed_in = await client.sign_in(
                    phone_number=phone_number,
                    phone_code_hash=phone_code_hash,
                    phone_code=otp_code
                )
            except SessionPasswordNeeded:
                # Требуется пароль для 2FA
                if not password:
                    await client.disconnect()
                    return False, None, "Password required for 2FA"
                
                # Проверка пароля
                signed_in = await client.check_password(password)
            
            # Получение session string
            session_string = await client.export_session_string()
            
            await client.disconnect()
            
            logger.info(f"Successfully authenticated {phone_number}")
            return True, session_string, None
            
        except PhoneCodeInvalid:
            error_msg = "Invalid OTP code"
            logger.warning(error_msg)
            return False, None, error_msg
        except PhoneCodeExpired:
            error_msg = "OTP code expired"
            logger.warning(error_msg)
            return False, None, error_msg
        except FloodWait as e:
            error_msg = f"FloodWait: {e.value} seconds"
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
            client = Client(
                name="temp_check",
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
                workdir="/tmp/hydrogram_sessions"
            )
            
            await client.start()
            me = await client.get_me()
            await client.stop()
            
            account_info = {
                'id': me.id,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone_number': me.phone_number,
            }
            
            return True, account_info, None
            
        except AuthKeyUnregistered:
            error_msg = "Session expired or invalid"
            logger.warning(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Error getting account info: {str(e)}"
            logger.exception(error_msg)
            return False, None, error_msg
