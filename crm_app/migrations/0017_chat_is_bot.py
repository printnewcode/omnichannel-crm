from django.db import migrations, models


def classify_telegram_bots(apps, schema_editor):
    Chat = apps.get_model('crm_app', 'Chat')
    # Telegram bot usernames conventionally end in "bot". This data migration
    # hides already stored bot peers; subsequent Telethon events use entity.bot.
    Chat.objects.filter(
        telegram_account__account_type='personal',
        chat_type='private',
        username__iendswith='bot',
    ).update(is_bot=True)
    # The service contact is an automated Telegram peer without a bot username.
    Chat.objects.filter(
        telegram_account__account_type='personal',
        chat_type='private',
        telegram_id=777000,
    ).update(is_bot=True)
    Chat.objects.filter(
        telegram_account__account_type='personal',
        chat_type='private',
        title__icontains='bot',
    ).update(is_bot=True)


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0016_classify_existing_max_groups'),
    ]

    operations = [
        migrations.AddField(
            model_name='chat',
            name='is_bot',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Собеседник — бот'),
        ),
        migrations.RunPython(classify_telegram_bots, migrations.RunPython.noop),
    ]