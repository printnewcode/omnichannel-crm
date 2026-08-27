"""Durable database outbox for messages sent by connector processes."""

import logging
import mimetypes
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Chat, OutboundDelivery
from .message_router import MessageRouter
from .realtime import publish_delivery, publish_message

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = (OutboundDelivery.Status.PENDING, OutboundDelivery.Status.RETRY)


def enqueue_delivery(
    *, chat, text, media_path=None, reply_to_message=None, requested_by=None,
    idempotency_key=None, origin=OutboundDelivery.Origin.OPERATOR,
):
    values = {
        'chat': chat,
        'text': text or '',
        'media_path': media_path,
        'reply_to_message': reply_to_message,
        'requested_by': requested_by,
        'origin': origin,
    }
    if idempotency_key is None:
        return OutboundDelivery.objects.create(**values)
    delivery, _ = OutboundDelivery.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults=values,
    )
    return delivery


def enqueue_reaction(*, message, emoji, requested_by=None):
    return OutboundDelivery.objects.create(
        chat=message.chat,
        reply_to_message=message,
        reaction_emoji=emoji,
        requested_by=requested_by,
        text='',
    )


def recover_stale_deliveries(stale_after_seconds=300):
    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    return OutboundDelivery.objects.filter(
        status=OutboundDelivery.Status.PROCESSING,
        updated_at__lt=cutoff,
    ).update(
        status=OutboundDelivery.Status.RETRY,
        available_at=timezone.now(),
        last_error='Connector stopped while processing; retry scheduled',
    )


def _claim_next():
    with transaction.atomic():
        delivery = (
            OutboundDelivery.objects.select_for_update(skip_locked=True)
            .select_related('chat', 'chat__telegram_account', 'reply_to_message')
            .filter(status__in=RETRYABLE_STATUSES, available_at__lte=timezone.now())
            .order_by('available_at', 'id')
            .first()
        )
        if not delivery:
            return None
        delivery.status = OutboundDelivery.Status.PROCESSING
        delivery.attempts = F('attempts') + 1
        delivery.last_error = ''
        delivery.save(update_fields=['status', 'attempts', 'last_error', 'updated_at'])
        delivery.refresh_from_db()
        return delivery


def process_next_delivery():
    delivery = _claim_next()
    if not delivery:
        return False

    router = MessageRouter()
    try:
        if delivery.reaction_emoji:
            from .reactions import set_actor_reaction

            if not delivery.reply_to_message_id:
                raise RuntimeError('Reaction target is missing')
            if not router.send_reaction(delivery.reply_to_message, delivery.reaction_emoji):
                raise RuntimeError('Provider did not confirm reaction')
            target = set_actor_reaction(
                delivery.reply_to_message_id,
                'self',
                delivery.reaction_emoji,
                chosen=True,
            )
            OutboundDelivery.objects.filter(pk=delivery.pk).update(
                status=OutboundDelivery.Status.SENT,
                provider_message_id=(
                    str(target.telegram_id or target.external_message_id or target.id)
                ),
                last_error='',
            )
            delivery.refresh_from_db()
            publish_delivery(delivery)
            publish_message(target.id)
            return True

        if delivery.provider_message_id:
            # The provider accepted this item before a previous connector
            # stopped. Resume local persistence without sending it again.
            provider_message_id = delivery.provider_message_id
        elif delivery.reply_to_message_id:
            provider_message_id = router.send_reply(
                delivery.reply_to_message,
                delivery.text,
                delivery.media_path,
            )
        else:
            provider_message_id = router.send_message(
                delivery.chat,
                delivery.text,
                delivery.media_path,
            )

        if not provider_message_id:
            raise RuntimeError('Provider did not confirm message delivery')

        # Persist the provider acknowledgement before any further database
        # work so recovery cannot repeat an already accepted send.
        OutboundDelivery.objects.filter(pk=delivery.pk).update(
            provider_message_id=str(provider_message_id),
        )
        delivery.provider_message_id = str(provider_message_id)

        message = router.create_outgoing_message(
            chat=delivery.chat,
            text=delivery.text,
            telegram_message_id=provider_message_id,
            reply_to_message=delivery.reply_to_message,
            message_type=(
                'text' if not delivery.media_path else
                'photo' if (mimetypes.guess_type(delivery.media_path)[0] or '') in {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'} else
                'video' if (mimetypes.guess_type(delivery.media_path)[0] or '').startswith('video/') else
                'voice' if (mimetypes.guess_type(delivery.media_path)[0] or '').startswith('audio/') else
                'document'
            ),
            media_file_path=delivery.media_path,
        )
        message.metadata = {
            **(message.metadata or {}),
            'delivery_id': delivery.id,
            'message_origin': delivery.origin,
            **({'original_filename': Path(delivery.media_path).name} if delivery.media_path else {}),
        }
        message.save(update_fields=['metadata', 'updated_at'])
        if delivery.origin == OutboundDelivery.Origin.OPERATOR:
            from .ai_assistant import register_manual_outgoing
            register_manual_outgoing(message.id)
        if getattr(message, '_outbox_was_created', True):
            Chat.objects.filter(pk=delivery.chat_id).update(
                message_count=F('message_count') + 1,
                last_message_at=message.telegram_date,
            )
        OutboundDelivery.objects.filter(pk=delivery.pk).update(
            status=OutboundDelivery.Status.SENT,
            provider_message_id=provider_message_id,
            created_message=message,
            last_error='',
        )
        delivery.refresh_from_db()
        publish_delivery(delivery)
        publish_message(message.id)
        return True
    except Exception as exc:
        logger.exception('Outbound delivery %s failed', delivery.pk)
        if delivery.attempts >= 5:
            status = OutboundDelivery.Status.FAILED
            available_at = delivery.available_at
        else:
            status = OutboundDelivery.Status.RETRY
            delay = min(300, 2 ** max(1, delivery.attempts))
            available_at = timezone.now() + timedelta(seconds=delay)

        OutboundDelivery.objects.filter(pk=delivery.pk).update(
            status=status,
            available_at=available_at,
            last_error=str(exc)[:2000],
        )
        delivery.refresh_from_db()
        publish_delivery(delivery)
        return True
