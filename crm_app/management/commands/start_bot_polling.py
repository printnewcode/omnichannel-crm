"""
Management command to start polling for existing Telegram bots
"""
import asyncio
import logging
import signal
import sys
from django.core.management.base import BaseCommand
from django.conf import settings
from crm_app.services.bot_polling_service import polling_manager
from crm_app.models import TelegramAccount

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Start polling messages from existing Telegram bots'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bot-tokens',
            nargs='+',
            help='Specific bot tokens to poll (if not provided, polls all active bots)'
        )
        parser.add_argument(
            '--stop',
            action='store_true',
            help='Stop polling instead of starting'
        )

    def handle(self, *args, **options):
        if options['stop']:
            self.stdout.write('Stopping bot polling...')
            # Note: This would need additional implementation to gracefully stop
            self.stdout.write(self.style.SUCCESS('Bot polling stopped'))
            return

        # Get bot tokens
        bot_tokens = options.get('bot_tokens')
        if not bot_tokens:
            # Get all active bot accounts from database
            bot_accounts = TelegramAccount.objects.filter(
                account_type=TelegramAccount.AccountType.BOT,
                status=TelegramAccount.AccountStatus.ACTIVE,
                bot_token__isnull=False
            )
            bot_tokens = [account.bot_token for account in bot_accounts]

        if not bot_tokens:
            self.stdout.write(
                self.style.WARNING('No bot tokens found. Add bot accounts in admin panel first.')
            )
            return

        self.stdout.write(f'Starting polling for {len(bot_tokens)} bot(s)...')

        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self.stdout.write('\nShutting down polling...')
            for task in asyncio.all_tasks():
                task.cancel()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start polling
        async def run_polling():
            try:
                # Add all bots
                for token in bot_tokens:
                    await polling_manager.add_bot(token)
                    self.stdout.write(f'Added polling for bot: {token[:10]}...')

                self.stdout.write(self.style.SUCCESS('Bot polling started. Press Ctrl+C to stop.'))

                # Keep running
                while True:
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Polling error: {e}")
                self.stdout.write(self.style.ERROR(f'Polling error: {e}'))

        # Run the async polling
        try:
            asyncio.run(run_polling())
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS('Bot polling stopped'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))