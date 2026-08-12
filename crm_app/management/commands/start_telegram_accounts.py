"""Run the long-lived Telethon connector process."""

import logging
import signal
import threading
import time

from django.core.management.base import BaseCommand

from crm_app.services.telegram_client_manager import TelegramClientManager
from crm_app.services.outbound_delivery import process_next_delivery, recover_stale_deliveries

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run persistent Telethon clients for all active personal accounts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reconcile-interval',
            type=int,
            default=900,
            help='Seconds between safety reconciliation passes (default: 900)',
        )
        parser.add_argument(
            '--account-refresh-interval',
            type=int,
            default=10,
            help='Seconds between Admin state reconciliation passes (default: 10)',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Start clients and run one reconciliation pass, then exit (diagnostics)',
        )

    def handle(self, *args, **options):
        manager = TelegramClientManager()
        stop_event = threading.Event()

        def request_stop(signum, frame):
            self.stdout.write(self.style.WARNING(f'Received signal {signum}; stopping connector...'))
            stop_event.set()

        for signal_name in ('SIGTERM', 'SIGINT'):
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), request_stop)

        self.stdout.write(self.style.SUCCESS('Starting persistent Telegram connector...'))
        manager.start_all_active_sync()

        if options['once']:
            manager.sync_all_active_sync()
            manager.wait_for_catchups_sync(timeout=120)
            manager.run_async_sync(manager.stop_all())
            return

        refresh_interval = max(10, options['account_refresh_interval'])
        reconcile_interval = max(60, options['reconcile_interval'])
        next_refresh = time.monotonic() + refresh_interval
        next_reconcile = time.monotonic() + reconcile_interval
        recover_stale_deliveries()

        try:
            while not stop_event.wait(1.0):
                now = time.monotonic()

                if now >= next_refresh:
                    # Apply start, stop, and restart requests recorded by Django Admin.
                    manager.start_all_active_sync()
                    next_refresh = now + refresh_interval

                # Drain a bounded batch so account health checks are never starved.
                for _ in range(25):
                    if not process_next_delivery():
                        break

                if now >= next_reconcile:
                    # This is a low-frequency safety pass, not the primary delivery path.
                    manager.sync_all_active_sync()
                    next_reconcile = now + reconcile_interval
        finally:
            try:
                manager.run_async_sync(manager.stop_all())
            except Exception:
                logger.exception('Failed to stop all Telethon clients cleanly')
            self.stdout.write(self.style.SUCCESS('Telegram connector stopped'))