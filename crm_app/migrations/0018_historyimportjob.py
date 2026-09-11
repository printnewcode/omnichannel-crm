from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('crm_app', '0017_chat_is_bot'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoryImportJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('chat_history', 'История диалога'), ('chat_discovery', 'Загрузка диалогов')], max_length=30)),
                ('status', models.CharField(choices=[('pending', 'Ожидает'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=20)),
                ('parameters', models.JSONField(blank=True, default=dict)),
                ('progress_current', models.PositiveIntegerField(default=0)),
                ('progress_total', models.PositiveIntegerField(blank=True, null=True)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history_import_jobs', to='crm_app.telegramaccount')),
                ('chat', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='history_import_jobs', to='crm_app.chat')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='historyimportjob',
            index=models.Index(fields=['status', 'created_at'], name='crm_app_his_status_0cdbfb_idx'),
        ),
    ]
