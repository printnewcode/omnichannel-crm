"""
Polling service for existing Telegram bots
Fetches messages using getUpdates API instead of webhooks
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import aiohttp
from django.conf import settings
from django.db import close_old_connections
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from ..models import TelegramAccount, Chat, Message

logger = logging.getLogger(__name__)


class BotPollingService:
    """Service for polling messages from existing Telegram bots"""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = 0
        self.is_running = False
        self.poll_interval = 1  # seconds between polls
        self.timeout = 30  # long polling timeout

    async def get_updates(self) -> Optional[Dict[str, Any]]:
        """Fetch updates from Telegram Bot API"""
        try:
            params = {
                'offset': self.last_update_id + 1,
                'timeout': self.timeout,
                'allowed_updates': ['message', 'edited_message', 'callback_query']
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/getUpdates", params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Failed to get updates: {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Error fetching updates: {e}")
            return None

    async def process_update(self, update: Dict[str, Any]) -> None:
        """Process a single update from Telegram"""
        try:
            # Update offset
            if 'update_id' in update:
                self.last_update_id = update['update_id']

            # Handle message
            if 'message' in update:
                await self.process_message(update['message'])
            elif 'edited_message' in update:
                await self.process_edited_message(update['edited_message'])
            elif 'callback_query' in update:
                await self.process_callback_query(update['callback_query'])

        except Exception as e:
            logger.error(f"Error processing update: {e}")

    async def process_message(self, message_data: Dict[str, Any]) -> None:
        """Process incoming message"""
        try:
            # Find or create chat
            chat_data = message_data.get('chat', {})
            chat_id = chat_data.get('id')

            # Get or create TelegramAccount for this bot
            account = await database_sync_to_async(self.get_or_create_bot_account)()

            # Get or create chat
            chat, created = await database_sync_to_async(Chat.objects.get_or_create)(
                telegram_id=chat_id,
                telegram_account=account,
                defaults={
                    'chat_type': self.get_chat_type(chat_data),
                    'title': chat_data.get('title'),
                    'username': chat_data.get('username'),
                    'first_name': chat_data.get('first_name'),
                    'last_name': chat_data.get('last_name'),
                }
            )

            # Create message
            message = await database_sync_to_async(Message.objects.create)(
                telegram_id=message_data['message_id'],
                chat=chat,
                message_type=self.get_message_type(message_data),
                status=Message.MessageStatus.RECEIVED,
                text=message_data.get('text'),
                from_user_id=message_data.get('from', {}).get('id'),
                from_user_name=self.get_user_display_name(message_data.get('from', {})),
                from_user_username=message_data.get('from', {}).get('username'),
                telegram_date=datetime.fromtimestamp(message_data['date']),
                is_outgoing=False,
                media_caption=message_data.get('caption'),
                metadata=message_data  # Store full message data
            )

            logger.info(f"Processed message {message.telegram_id} in chat {chat_id}")

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def process_edited_message(self, message_data: Dict[str, Any]) -> None:
        """Process edited message"""
        # Update existing message with new text
        pass

    async def process_callback_query(self, callback_data: Dict[str, Any]) -> None:
        """Process callback query"""
        # Handle inline keyboard callbacks
        pass

    def get_or_create_bot_account(self) -> TelegramAccount:
        """Get or create bot account in database"""
        account, created = TelegramAccount.objects.get_or_create(
            bot_token=self.bot_token,
            defaults={
                'name': f"Bot {self.bot_token[:10]}...",
                'account_type': TelegramAccount.AccountType.BOT,
                'status': TelegramAccount.AccountStatus.ACTIVE,
            }
        )
        return account

    def get_chat_type(self, chat_data: Dict[str, Any]) -> str:
        """Convert Telegram chat type to our enum"""
        chat_type = chat_data.get('type', 'private')
        mapping = {
            'private': Chat.ChatType.PRIVATE,
            'group': Chat.ChatType.GROUP,
            'supergroup': Chat.ChatType.SUPERGROUP,
            'channel': Chat.ChatType.CHANNEL,
        }
        return mapping.get(chat_type, Chat.ChatType.PRIVATE)

    def get_message_type(self, message_data: Dict[str, Any]) -> str:
        """Determine message type"""
        if message_data.get('text'):
            return Message.MessageType.TEXT
        elif message_data.get('photo'):
            return Message.MessageType.PHOTO
        elif message_data.get('video'):
            return Message.MessageType.VIDEO
        elif message_data.get('voice'):
            return Message.MessageType.VOICE
        elif message_data.get('document'):
            return Message.MessageType.DOCUMENT
        elif message_data.get('sticker'):
            return Message.MessageType.STICKER
        else:
            return Message.MessageType.OTHER

    def get_user_display_name(self, user_data: Dict[str, Any]) -> str:
        """Get display name for user"""
        if not user_data:
            return "Unknown"

        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')

        if first_name and last_name:
            return f"{first_name} {last_name}"
        elif first_name:
            return first_name
        elif last_name:
            return last_name
        else:
            return f"User {user_data.get('id', 'unknown')}"

    async def send_message(self, chat_id: int, text: str, **kwargs) -> bool:
        """Send message via bot"""
        try:
            data = {
                'chat_id': chat_id,
                'text': text,
                **kwargs
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/sendMessage", json=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('ok'):
                            logger.info(f"Message sent to chat {chat_id}")
                            return True
                    logger.error(f"Failed to send message: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    async def start_polling(self) -> None:
        """Start polling for updates"""
        self.is_running = True
        logger.info(f"Started polling for bot {self.bot_token[:10]}...")

        while self.is_running:
            try:
                # Ensure connection is fresh
                await database_sync_to_async(close_old_connections)()
                updates = await self.get_updates()
                if updates and updates.get('ok') and updates.get('result'):
                    for update in updates['result']:
                        await self.process_update(update)

            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)  # Wait before retry

            await asyncio.sleep(self.poll_interval)

    def stop_polling(self) -> None:
        """Stop polling"""
        self.is_running = False
        logger.info("Stopped polling")


class BotPollingManager:
    """Manager for multiple bot polling services"""

    def __init__(self):
        self.services: Dict[str, BotPollingService] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    async def add_bot(self, bot_token: str) -> None:
        """Add a bot for polling"""
        if bot_token not in self.services:
            service = BotPollingService(bot_token)
            self.services[bot_token] = service

            # Start polling task
            task = asyncio.create_task(service.start_polling())
            self.tasks[bot_token] = task
            logger.info(f"Added polling for bot {bot_token[:10]}...")

    async def remove_bot(self, bot_token: str) -> None:
        """Remove a bot from polling"""
        if bot_token in self.services:
            self.services[bot_token].stop_polling()
            if bot_token in self.tasks:
                self.tasks[bot_token].cancel()
                del self.tasks[bot_token]
            del self.services[bot_token]
            logger.info(f"Removed polling for bot {bot_token[:10]}...")

    async def send_message(self, bot_token: str, chat_id: int, text: str, **kwargs) -> bool:
        """Send message via specific bot"""
        if bot_token in self.services:
            return await self.services[bot_token].send_message(chat_id, text, **kwargs)
        return False

    def get_active_bots(self) -> list:
        """Get list of active bot tokens"""
        return list(self.services.keys())


# Global manager instance
polling_manager = BotPollingManager()