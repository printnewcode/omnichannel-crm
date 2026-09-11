"""Provider-neutral reaction metadata helpers."""

from collections import Counter

from django.db import transaction

from ..models import Message


ALLOWED_REACTIONS = ('👍', '❤️', '🔥', '👏', '😁', '🎉', '😢', '🤔', '👎')


def normalize_reaction(value):
    emoji = str(value or '').strip()
    return emoji if emoji in ALLOWED_REACTIONS else ''


def telegram_reaction_summary(reactions):
    """Convert Telethon MessageReactions into the compact API representation."""
    summary = []
    for item in getattr(reactions, 'results', None) or []:
        reaction = getattr(item, 'reaction', None)
        emoji = getattr(reaction, 'emoticon', None)
        if not emoji:
            document_id = getattr(reaction, 'document_id', None)
            emoji = f'custom:{document_id}' if document_id else None
        if not emoji:
            continue
        summary.append({
            'emoji': str(emoji),
            'count': max(1, int(getattr(item, 'count', 1) or 1)),
            'chosen': getattr(item, 'chosen_order', None) is not None,
        })
    return summary


@transaction.atomic
def set_reaction_summary(message_id, summary):
    message = Message.objects.select_for_update().get(pk=message_id)
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    message.metadata = {**metadata, 'reactions': list(summary or [])}
    message.save(update_fields=['metadata', 'updated_at'])
    return message


@transaction.atomic
def set_actor_reaction(message_id, actor_key, emoji, *, chosen=False):
    """Apply a personal-chat reaction event and rebuild aggregate counters."""
    message = Message.objects.select_for_update().get(pk=message_id)
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    actors = metadata.get('reaction_actors')
    actors = dict(actors) if isinstance(actors, dict) else {}
    actor_key = str(actor_key or 'peer')
    emoji = str(emoji or '').strip()
    if emoji:
        actors[actor_key] = {'emoji': emoji, 'chosen': bool(chosen)}
    else:
        actors.pop(actor_key, None)

    counts = Counter(
        value.get('emoji') for value in actors.values()
        if isinstance(value, dict) and value.get('emoji')
    )
    selected = {
        value.get('emoji') for value in actors.values()
        if isinstance(value, dict) and value.get('chosen')
    }
    reactions = [
        {'emoji': reaction, 'count': count, 'chosen': reaction in selected}
        for reaction, count in counts.items()
    ]
    message.metadata = {
        **metadata,
        'reaction_actors': actors,
        'reactions': reactions,
    }
    message.save(update_fields=['metadata', 'updated_at'])
    return message
