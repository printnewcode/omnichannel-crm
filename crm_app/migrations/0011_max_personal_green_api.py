from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0010_green_api_whatsapp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='telegramaccount',
            name='account_type',
            field=models.CharField(
                choices=[
                    ('personal', 'Личный аккаунт (Telethon)'),
                    ('bot', 'Бот (pyTelegramBotAPI)'),
                    ('whatsapp', 'WhatsApp через GREEN-API'),
                    ('max', 'MAX личный аккаунт через GREEN-API'),
                ],
                max_length=20,
                verbose_name='Тип аккаунта',
            ),
        ),
    ]