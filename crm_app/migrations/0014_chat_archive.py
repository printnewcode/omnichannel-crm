from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm_app', '0013_alter_telegramaccount_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='chat',
            name='is_archived',
            field=models.BooleanField(db_index=True, default=False, verbose_name='В архиве'),
        ),
        migrations.AddIndex(
            model_name='chat',
            index=models.Index(
                fields=['telegram_account', 'is_archived', 'last_message_at'],
                name='crm_chat_account_arch_idx',
            ),
        ),
    ]