"""
Management команда для запуска всех активных Telegram аккаунтов
"""
import asyncio
from django.core.management.base import BaseCommand
from crm_app.models import TelegramAccount
from crm_app.services.telegram_client_manager import TelegramClientManager


class Command(BaseCommand):
    help = 'Запуск всех активных Telegram аккаунтов (Telethon клиенты)'

    def handle(self, *args, **options):
        """Запуск всех активных аккаунтов"""
        self.stdout.write(self.style.SUCCESS('Запуск Telegram аккаунтов...'))
        
        manager = TelegramClientManager()
        
        # Получение всех активных личных аккаунтов
        accounts = TelegramAccount.objects.filter(
            account_type=TelegramAccount.AccountType.PERSONAL,
            status__in=[
                TelegramAccount.AccountStatus.ACTIVE,
                TelegramAccount.AccountStatus.INACTIVE
            ]
        )
        
        if not accounts.exists():
            self.stdout.write(self.style.WARNING('Нет активных аккаунтов для запуска'))
            return
        
        # Запуск всех аккаунтов через background loop
        try:
            for account in accounts:
                manager.start_client_sync(account)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Успешно запущено {len(accounts)} аккаунтов'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Ошибка при запуске аккаунтов: {e}')
            )
