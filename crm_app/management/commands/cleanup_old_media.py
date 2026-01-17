from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.db.models import Exists, OuterRef
from crm_app.models import Message, ChatAssignment
import os
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Удалить старые медиа файлы, сохранив file_id для повторного скачивания'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                          help='Удалить файлы старше N дней')
        parser.add_argument('--dry-run', action='store_true',
                          help='Показать что будет удалено')

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        cutoff_date = timezone.now() - timedelta(days=days)

        # Подзапрос для активных чатов
        active_chats_subquery = ChatAssignment.objects.filter(
            chat=OuterRef('chat'),
            is_active=True,
            operator__is_active=True
        )

        old_messages = Message.objects.filter(
            media_file_path__isnull=False,  # Есть файл на диске
            telegram_file_id__isnull=False,  # Есть file_id для повторного скачивания
            telegram_date__lt=cutoff_date,   # Старое сообщение
        ).exclude(
            # Исключить сообщения в активных чатах
            Exists(active_chats_subquery)
        )

        self.stdout.write(f"Найдено {old_messages.count()} сообщений со старыми медиа")

        deleted_count = 0
        freed_space = 0

        for message in old_messages:
            file_path = settings.MEDIA_ROOT / message.media_file_path
            if file_path.exists():
                file_size = file_path.stat().st_size
                if dry_run:
                    self.stdout.write(f"Будет удален: {file_path} ({file_size} bytes)")
                else:
                    try:
                        file_path.unlink()
                        message.media_file_path = None  # Файл удален, но file_id сохранен
                        message.save(update_fields=['media_file_path'])
                        deleted_count += 1
                        freed_space += file_size
                    except Exception as e:
                        logger.error(f"Ошибка удаления {file_path}: {e}")

        if not dry_run:
            self.stdout.write(f"Удалено {deleted_count} файлов, освобождено {freed_space / 1024 / 1024:.1f} MB")
        else:
            self.stdout.write(f"В dry-run режиме будет удалено {old_messages.count()} файлов")