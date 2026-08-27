from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0026_relax_ai_prompt_for_general_conversation'),
    ]

    operations = [
        migrations.AddField(
            model_name='chat',
            name='ai_disabled',
            field=models.BooleanField(db_index=True, default=False, verbose_name='ИИ отключён для чата'),
        ),
    ]
