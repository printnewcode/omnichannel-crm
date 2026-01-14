"""
Webhook forwarder for existing Telegram bots
Forwards webhooks from existing bot infrastructure to CRM
"""
import asyncio
import logging
import json
from typing import Dict, Any, Optional
import aiohttp
from django.conf import settings
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class WebhookForwarder:
    """
    Forwards webhooks from existing bot to CRM webhook endpoint
    Useful when your bot is already running on another server/domain
    """

    def __init__(self, crm_base_url: str, bot_token: str):
        self.crm_base_url = crm_base_url.rstrip('/')
        self.bot_token = bot_token
        self.webhook_url = f"{self.crm_base_url}/api/webhook/bot/{bot_token}/"
        self.is_running = False

    async def forward_webhook(self, update_data: Dict[str, Any]) -> bool:
        """
        Forward webhook update to CRM

        Usage in your existing bot:
        ```python
        from webhook_forwarder import WebhookForwarder

        forwarder = WebhookForwarder("https://your-crm-domain.com", "your-bot-token")

        @bot.message_handler(func=lambda message: True)
        def handle_message(message):
            # Your existing logic here
            # ...

            # Forward to CRM
            asyncio.create_task(forwarder.forward_webhook({
                'message': {
                    'message_id': message.message_id,
                    'from': {
                        'id': message.from_user.id,
                        'first_name': message.from_user.first_name,
                        'last_name': message.from_user.last_name,
                        'username': message.from_user.username,
                    },
                    'chat': {
                        'id': message.chat.id,
                        'type': message.chat.type,
                        'title': getattr(message.chat, 'title', None),
                        'username': getattr(message.chat, 'username', None),
                        'first_name': getattr(message.chat, 'first_name', None),
                        'last_name': getattr(message.chat, 'last_name', None),
                    },
                    'date': message.date,
                    'text': message.text,
                }
            }))
        ```
        """
        try:
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'TelegramBot-WebhookForwarder/1.0'
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=update_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"Successfully forwarded webhook to CRM")
                        return True
                    else:
                        response_text = await response.text()
                        logger.error(f"Failed to forward webhook: {response.status} - {response_text}")
                        return False

        except Exception as e:
            logger.error(f"Error forwarding webhook: {e}")
            return False


class WebhookBridge:
    """
    HTTP server that receives webhooks from existing bot
    and forwards them to CRM
    """

    def __init__(self, listen_port: int = 8081, crm_url: str = None):
        self.listen_port = listen_port
        self.crm_url = crm_url or settings.CRM_BASE_URL
        self.forwarders: Dict[str, WebhookForwarder] = {}
        self.is_running = False

    def add_bot(self, bot_token: str) -> None:
        """Add a bot for webhook forwarding"""
        if bot_token not in self.forwarders:
            self.forwarders[bot_token] = WebhookForwarder(self.crm_url, bot_token)
            logger.info(f"Added webhook forwarder for bot {bot_token[:10]}...")

    def remove_bot(self, bot_token: str) -> None:
        """Remove a bot from webhook forwarding"""
        if bot_token in self.forwarders:
            del self.forwarders[bot_token]
            logger.info(f"Removed webhook forwarder for bot {bot_token[:10]}...")

    async def handle_webhook(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Handle incoming webhook from bot"""
        try:
            # Extract bot token from URL path
            path_parts = request.path.split('/')
            bot_token = None

            # Try different URL patterns
            if len(path_parts) >= 3 and path_parts[-2] == 'webhook':
                # URL format: /webhook/bot/{token}/
                bot_token = path_parts[-1]
            elif 'token' in request.query:
                # URL format: /webhook/bot/?token={token}
                bot_token = request.query['token']

            if not bot_token:
                return aiohttp.web.json_response(
                    {'error': 'Bot token required'},
                    status=400
                )

            if bot_token not in self.forwarders:
                return aiohttp.web.json_response(
                    {'error': 'Bot not registered'},
                    status=404
                )

            # Get webhook data
            update_data = await request.json()

            # Forward to CRM
            success = await self.forwarders[bot_token].forward_webhook(update_data)

            if success:
                return aiohttp.web.json_response({'status': 'ok'})
            else:
                return aiohttp.web.json_response(
                    {'error': 'Failed to forward to CRM'},
                    status=500
                )

        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
            return aiohttp.web.json_response(
                {'error': 'Internal server error'},
                status=500
            )

    async def start_bridge(self) -> None:
        """Start the webhook bridge server"""
        app = aiohttp.web.Application()
        app.router.add_post('/webhook/bot/{token}/', self.handle_webhook)
        app.router.add_post('/webhook/bot/', self.handle_webhook)

        runner = aiohttp.web.AppRunner(app)
        await runner.setup()

        site = aiohttp.web.TCPSite(runner, '0.0.0.0', self.listen_port)
        await site.start()

        logger.info(f"Webhook bridge started on port {self.listen_port}")
        logger.info(f"Forwarding webhooks to: {self.crm_url}")

        self.is_running = True

        # Keep running
        while self.is_running:
            await asyncio.sleep(1)

    def stop_bridge(self) -> None:
        """Stop the webhook bridge"""
        self.is_running = False
        logger.info("Webhook bridge stopped")


# Example usage in your existing bot code
WEBHOOK_FORWARDER_EXAMPLE = '''
# In your existing bot code (e.g., bot.py)

import asyncio
from webhook_forwarder import WebhookForwarder

# Initialize forwarder
CRM_URL = "https://your-crm-domain.com"  # Your CRM domain
BOT_TOKEN = "your-bot-token-here"

forwarder = WebhookForwarder(CRM_URL, BOT_TOKEN)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Your existing message handling logic
    print(f"Received: {message.text}")

    # Forward message to CRM
    update_data = {
        'message': {
            'message_id': message.message_id,
            'from': {
                'id': message.from_user.id,
                'first_name': message.from_user.first_name,
                'last_name': message.from_user.last_name,
                'username': message.from_user.username,
            },
            'chat': {
                'id': message.chat.id,
                'type': message.chat.type,
                'title': getattr(message.chat, 'title', None),
                'username': getattr(message.chat, 'username', None),
                'first_name': getattr(message.chat, 'first_name', None),
                'last_name': getattr(message.chat, 'last_name', None),
            },
            'date': message.date,
            'text': message.text,
        }
    }

    # Forward asynchronously
    asyncio.create_task(forwarder.forward_webhook(update_data))

# For edited messages
@bot.edited_message_handler(func=lambda message: True)
def handle_edited_message(message):
    update_data = {
        'edited_message': {
            'message_id': message.message_id,
            'from': {
                'id': message.from_user.id,
                'first_name': message.from_user.first_name,
                'last_name': message.from_user.last_name,
                'username': message.from_user.username,
            },
            'chat': {
                'id': message.chat.id,
                'type': message.chat.type,
                'title': getattr(message.chat, 'title', None),
                'username': getattr(message.chat, 'username', None),
                'first_name': getattr(message.chat, 'first_name', None),
                'last_name': getattr(message.chat, 'last_name', None),
            },
            'date': message.date,
            'edit_date': message.edit_date,
            'text': message.text,
        }
    }

    asyncio.create_task(forwarder.forward_webhook(update_data))

# Start your bot as usual
bot.polling()
'''