from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('crm_app', '0020_chat_visible_date_index')]

    operations = [
        migrations.AddField(
            model_name='outbounddelivery',
            name='reaction_emoji',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
