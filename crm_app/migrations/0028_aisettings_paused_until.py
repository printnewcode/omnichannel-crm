from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0027_chat_ai_disabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='aisettings',
            name='paused_until',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='Глобальная пауза ИИ до',
            ),
        ),
    ]
