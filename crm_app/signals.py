"""
Сигналы Django для автоматического создания/обновления связанных объектов
"""
# Message counters are updated atomically by ingestion and outbox services.
# Operator profiles and chat assignments are retained only for schema compatibility;
# the shared queue uses ordinary authenticated Django users.
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Chat


@receiver(post_save, sender=Chat)
def match_new_chat_with_cached_google_contact(sender, instance, created, **kwargs):
    """New chats use the local contact cache and never call Google directly."""
    if not created:
        return
    from .services.google_contacts import match_chat_contact

    transaction.on_commit(lambda chat_id=instance.id: match_chat_contact(Chat.objects.get(pk=chat_id)))
