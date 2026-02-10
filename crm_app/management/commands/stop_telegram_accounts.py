"""
Management команда для остановки всех Telegram аккаунтов
"""
import asyncio
from django.core.management.base import BaseCommand
from crm_app.services.telegram_client_manager import TelegramClientManager


class Command(BaseCommand):
    help = 'Остановка всех запущенных Telegram аккаунтов'

    def handle(self, *args, **options):
        """Остановка всех аккаунтов"""
        self.stdout.write(self.style.SUCCESS('Остановка Telegram аккаунтов...'))
        
        manager = TelegramClientManager()
        running_accounts = manager.get_running_accounts()
        
        if not running_accounts:
            self.stdout.write(self.style.WARNING('Нет запущенных аккаунтов'))
            return
        
        try:
            manager.run_async_sync(manager.stop_all())
            self.stdout.write(
                self.style.SUCCESS(
                    f'Успешно остановлено {len(running_accounts)} аккаунтов'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Ошибка при остановке аккаунтов: {e}')
            )
