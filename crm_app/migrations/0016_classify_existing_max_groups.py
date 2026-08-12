from django.db import migrations


def classify_existing_max_groups(apps, schema_editor):
    Chat = apps.get_model('crm_app', 'Chat')
    for chat in Chat.objects.filter(telegram_account__account_type='max').iterator(chunk_size=500):
        external_id = str((chat.metadata or {}).get('external_chat_id') or '')
        if external_id.startswith('-') and chat.chat_type != 'group':
            chat.chat_type = 'group'
            chat.save(update_fields=['chat_type', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0015_shared_queue_cleanup'),
    ]

    operations = [
        migrations.RunPython(classify_existing_max_groups, migrations.RunPython.noop),
    ]