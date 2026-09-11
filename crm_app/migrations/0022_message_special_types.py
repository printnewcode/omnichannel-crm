from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('crm_app', '0021_outbounddelivery_reaction_emoji')]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='message_type',
            field=models.CharField(
                choices=[
                    ('text', 'Текст'), ('photo', 'Фото'), ('video', 'Видео'),
                    ('voice', 'Голосовое'), ('audio', 'Аудио'),
                    ('document', 'Документ'), ('sticker', 'Стикер'),
                    ('location', 'Локация'), ('contact', 'Контакт'),
                    ('poll', 'Опрос'), ('service', 'Служебное сообщение'),
                    ('other', 'Другое'),
                ],
                default='text', max_length=20, verbose_name='Тип сообщения',
            ),
        ),
    ]
