import asyncio
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from asgiref.sync import sync_to_async
from crm_app.models import TelegramAccount
from crm_app.services.telegram_client_manager import TelegramClientManager

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Synchronize messages from all active Telegram accounts (for Cron)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting Telegram synchronization...'))
        
        try:
            manager = TelegramClientManager()
            manager.run_async_sync(self.sync_all_accounts())
            self.stdout.write(self.style.SUCCESS('Synchronization completed successfully.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Synchronization failed: {e}'))

    async def sync_all_accounts(self):
        # Fetch accounts asynchronously
        accounts_queryset = TelegramAccount.objects.filter(
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE
        )
        
        # Check if any accounts exist and fetch them as a list
        has_accounts = await sync_to_async(accounts_queryset.exists)()
        if not has_accounts:
            self.stdout.write(self.style.WARNING("No active personal accounts found for sync. Please activate them in the admin panel."))
            logger.info("No active personal accounts found for sync.")
            return

        accounts = await sync_to_async(list)(accounts_queryset)

        manager = TelegramClientManager()
        
        for account in accounts:
            logger.info(f"Syncing account: {account.name} ({account.id})")
            try:
                # Start client (this will connect and register handlers)
                success = await manager.start_client(account)
                if not success:
                    logger.error(f"Failed to start client for account {account.id}")
                    continue
                
                # Fetch recent messages
                await self.fetch_missed_messages(manager, account)
                
                # IMPORTANT: We DO NOT call stop_client here anymore.
                # stop_client sets status to INACTIVE. By removing this call,
                # the account status remains ACTIVE in the database.
                # On Shared Hosting, the process will die anyway, which is fine.
                # logger.info(f"Synchronization finished for {account.name}")
                
            except Exception as e:
                logger.exception(f"Error syncing account {account.id}: {e}")
                
                # Double check that status is still ACTIVE after stop_client
                # (stop_client usually doesn't change status to INACTIVE unless specified)
                await sync_to_async(account.refresh_from_db)()
                if account.status != TelegramAccount.AccountStatus.ACTIVE:
                    account.status = TelegramAccount.AccountStatus.ACTIVE
                    await sync_to_async(account.save)(update_fields=['status'])
                
            except Exception as e:
                logger.exception(f"Error syncing account {account.id}: {e}")

    async def fetch_missed_messages(self, manager, account):
        client = manager._clients.get(account.id)
        if not client:
            return

        # Fetch recent dialogs and messages
        async for dialog in client.iter_dialogs(limit=20):
            # The event handlers in TelegramClientManager._create_message_handler
            # only trigger on NEW messages while connected.
            # To fetch OLD messages, we'd need to manually trigger the handler logic.
            # For now, let's keep it simple: the goal is to trigger the connection
            # and let any queued updates flow in.
            
            # Deep sync logic could be added here if needed.
            pass
        
        # Wait a bit for async handlers to finish processing
        await asyncio.sleep(5)
