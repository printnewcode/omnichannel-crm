from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm_app', '0007_outbounddelivery'),
    ]

    operations = [
        migrations.AlterField(
            model_name='telegramaccount',
            name='account_type',
            field=models.CharField(choices=[('personal', 'Личный аккаунт (Telethon)'), ('bot', 'Бот (pyTelegramBotAPI)'), ('whatsapp', 'WhatsApp Business Cloud API'), ('max', 'MAX Bot API')], max_length=20, verbose_name='Тип аккаунта'),
        ),
        migrations.AddField(model_name='telegramaccount', name='access_token', field=models.TextField(blank=True, null=True)),
        migrations.AddField(model_name='telegramaccount', name='webhook_secret', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='telegramaccount', name='webhook_verify_token', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='telegramaccount', name='app_secret', field=models.CharField(blank=True, max_length=255, null=True)),
        migrations.AddField(model_name='telegramaccount', name='phone_number_id', field=models.CharField(blank=True, db_index=True, max_length=100, null=True)),
        migrations.AddField(model_name='telegramaccount', name='business_account_id', field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name='telegramaccount', name='api_version', field=models.CharField(blank=True, default='v23.0', max_length=32)),
        migrations.AlterField(
            model_name='message',
            name='telegram_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='Telegram Message ID'),
        ),
        migrations.AddField(
            model_name='message',
            name='external_message_id',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name='outbounddelivery',
            name='provider_message_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(condition=models.Q(external_message_id__isnull=False), fields=('chat', 'external_message_id'), name='unique_external_message_per_chat'),
        ),
    ]