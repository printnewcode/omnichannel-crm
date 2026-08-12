from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0005_message_telegram_file_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramaccount',
            name='bridge_secret',
            field=models.CharField(
                blank=True,
                help_text='Общий секрет для HMAC-подписи запросов между ботом и CRM',
                max_length=255,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='telegramaccount',
            name='bridge_url',
            field=models.CharField(
                blank=True,
                help_text='URL JGET bridge для отправки ответа; поддерживает {question_id}',
                max_length=500,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='chat',
            name='telegram_id',
            field=models.BigIntegerField(db_index=True, verbose_name='Telegram Chat ID'),
        ),
    ]
