"""
Сервисный слой для бизнес-логики
"""
from .telegram_client_manager import TelegramClientManager
from .message_router import MessageRouter

__all__ = ['TelegramClientManager', 'MessageRouter']
