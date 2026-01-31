import logging
from django.core.management.base import BaseCommand
from crm_app.tasks import cleanup_old_messages

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Clean up old messages (replaces Celery task for Shared Hosting)'

    def handle(self, *args, **options):
        self.stdout.write('Starting message cleanup...')
        try:
            count = cleanup_old_messages()
            self.stdout.write(self.style.SUCCESS(f'Successfully deleted {count} old messages.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Cleanup failed: {e}'))
