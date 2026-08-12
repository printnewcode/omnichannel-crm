# Generated for durable connector outbox.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm_app', '0006_telegramaccount_bridge_fields_and_chat_scope'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OutboundDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('idempotency_key', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('text', models.TextField(blank=True)),
                ('media_path', models.CharField(blank=True, max_length=500, null=True)),
                ('status', models.CharField(choices=[('pending', 'Ожидает отправки'), ('processing', 'Отправляется'), ('retry', 'Повторная попытка'), ('sent', 'Отправлено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=20)),
                ('provider_message_id', models.CharField(blank=True, max_length=255, null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('available_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('last_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('chat', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outbound_deliveries', to='crm_app.chat')),
                ('created_message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='outbound_delivery', to='crm_app.message')),
                ('reply_to_message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='outbound_delivery_replies', to='crm_app.message')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requested_message_deliveries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
                'indexes': [
                    models.Index(fields=['status', 'available_at'], name='crm_app_out_status_5a4c40_idx'),
                    models.Index(fields=['chat', 'created_at'], name='crm_app_out_chat_id_233f61_idx'),
                ],
            },
        ),
    ]