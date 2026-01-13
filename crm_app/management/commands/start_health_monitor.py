"""
Management команда для запуска сервиса мониторинга здоровья
"""
import asyncio
import os
import django
from django.core.management.base import BaseCommand
from django.conf import settings

# Ensure Django is set up
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')
django.setup()

from crm_app.services.health_monitor import HealthMonitor


class Command(BaseCommand):
    help = 'Запуск сервиса мониторинга здоровья системы'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daemon',
            action='store_true',
            help='Запустить в фоновом режиме',
        )

    def handle(self, *args, **options):
        """Запуск мониторинга здоровья"""
        self.stdout.write(self.style.SUCCESS('Запуск сервиса мониторинга здоровья...'))

        monitor = HealthMonitor()

        if options['daemon']:
            # Запуск в фоне
            self.stdout.write(self.style.SUCCESS('Мониторинг запущен в фоновом режиме'))
            try:
                asyncio.run(monitor.start_monitoring())
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING('Мониторинг остановлен пользователем'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Ошибка в мониторинге: {e}'))
        else:
            # Запуск однократной проверки
            self.stdout.write(self.style.SUCCESS('Выполнение однократной проверки здоровья...'))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                loop.run_until_complete(monitor._perform_health_checks())
                status = loop.run_until_complete(monitor.get_system_status())

                self.stdout.write(self.style.SUCCESS('Проверка завершена'))
                self.stdout.write(f"Статус системы: {status.get('status', 'unknown')}")
                self.stdout.write(f"Активных аккаунтов: {status.get('accounts', {}).get('active', 0)}")
                self.stdout.write(f"Запущенных клиентов: {status.get('accounts', {}).get('running_clients', 0)}")
                self.stdout.write(f"Всего чатов: {status.get('chats', 0)}")
                self.stdout.write(f"Всего сообщений: {status.get('messages', {}).get('total', 0)}")

                if status.get('messages', {}).get('failed', 0) > 0:
                    self.stdout.write(self.style.WARNING(f"Неудачных сообщений: {status['messages']['failed']}"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Ошибка при проверке: {e}'))
            finally:
                loop.close()