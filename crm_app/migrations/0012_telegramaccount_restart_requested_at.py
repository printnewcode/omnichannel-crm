from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0011_max_personal_green_api'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramaccount',
            name='restart_requested_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]