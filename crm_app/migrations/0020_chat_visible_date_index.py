from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0019_alter_telegramaccount_admin_labels'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='chat',
            index=models.Index(
                fields=['chat_type', 'is_bot', 'last_message_at'],
                name='crm_chat_visible_date_idx',
            ),
        ),
    ]
