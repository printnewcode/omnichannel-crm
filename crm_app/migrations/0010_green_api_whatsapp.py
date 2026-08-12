from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('crm_app', '0009_remove_message_unique_external_message_per_chat_and_more')]

    operations = [
        migrations.AlterField(
            model_name='telegramaccount',
            name='account_type',
            field=models.CharField(
                choices=[
                    ('personal', 'Личный аккаунт (Telethon)'),
                    ('bot', 'Бот (pyTelegramBotAPI)'),
                    ('whatsapp', 'WhatsApp через GREEN-API'),
                    ('max', 'MAX Bot API'),
                ],
                max_length=20,
                verbose_name='Тип аккаунта',
            ),
        ),
        migrations.AddField(
            model_name='telegramaccount', name='green_api_instance_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='telegramaccount', name='green_api_token',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='telegramaccount', name='green_webhook_token',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='telegramaccount', name='green_api_url',
            field=models.URLField(blank=True, default='https://api.green-api.com', max_length=500),
        ),
        migrations.AddField(
            model_name='telegramaccount', name='green_media_url',
            field=models.URLField(blank=True, default='https://media.green-api.com', max_length=500),
        ),
    ]
