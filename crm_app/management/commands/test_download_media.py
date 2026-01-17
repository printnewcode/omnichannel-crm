from django.core.management.base import BaseCommand
from crm_app.models import Message
from crm_app.services.telegram_client_manager import TelegramClientManager
import asyncio

class Command(BaseCommand):
    help = 'Test downloading media for a specific message'

    def add_arguments(self, parser):
        parser.add_argument('message_id', type=int, help='Message ID to download media for')

    def handle(self, *args, **options):
        message_id = options['message_id']

        async def download():
            try:
                message = await Message.objects.aget(id=message_id)
                self.stdout.write(f'Testing download for message {message.id}, type: {message.message_type}')

                manager = TelegramClientManager()
                result = await manager.download_media_by_message_id(message)
                self.stdout.write(self.style.SUCCESS(f'Success! Downloaded to: {result}'))

                # Проверить что файл сохранен
                message.refresh_from_db()
                self.stdout.write(f'Message media_file_path: {message.media_file_path}')

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error: {e}'))

        asyncio.run(download())