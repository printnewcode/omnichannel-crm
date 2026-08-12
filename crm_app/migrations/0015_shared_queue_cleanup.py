from django.db import migrations, models
from django.db.models import Count


def repair_chat_counters(apps, schema_editor):
    Chat = apps.get_model('crm_app', 'Chat')
    batch = []
    for chat in Chat.objects.annotate(real_message_count=Count('messages')).iterator(chunk_size=500):
        chat.message_count = chat.real_message_count
        # Historical read markers cannot be reconstructed reliably after the old
        # signal counted every incoming message again. Start from a clean state.
        chat.unread_count = 0
        batch.append(chat)
        if len(batch) >= 500:
            Chat.objects.bulk_update(batch, ['message_count', 'unread_count'])
            batch.clear()
    if batch:
        Chat.objects.bulk_update(batch, ['message_count', 'unread_count'])


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0014_chat_archive'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='chat',
            index=models.Index(
                fields=['chat_type', 'is_archived', 'last_message_at'],
                name='crm_chat_type_arch_idx',
            ),
        ),
        migrations.AlterField(
            model_name='message',
            name='message_type',
            field=models.CharField(
                choices=[
                    ('text', 'Текст'),
                    ('photo', 'Фото'),
                    ('video', 'Видео'),
                    ('voice', 'Голосовое'),
                    ('audio', 'Аудио'),
                    ('document', 'Документ'),
                    ('sticker', 'Стикер'),
                    ('location', 'Локация'),
                    ('contact', 'Контакт'),
                    ('other', 'Другое'),
                ],
                default='text',
                max_length=20,
                verbose_name='Тип сообщения',
            ),
        ),
        migrations.AlterModelOptions(
            name='outbounddelivery',
            options={
                'ordering': ['created_at'],
                'verbose_name': 'Исходящая отправка',
                'verbose_name_plural': 'Исходящие отправки',
            },
        ),
        migrations.RunPython(repair_chat_counters, migrations.RunPython.noop),
    ]