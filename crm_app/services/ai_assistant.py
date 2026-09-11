"""Event-driven AI auto-replies with per-chat isolation and strict deduplication."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import (
    AISettings,
    Chat,
    ChatAIState,
    Message,
    OperatorPresenceSession,
    OutboundDelivery,
    TelegramAccount,
)

logger = logging.getLogger(__name__)

SUPPORTED_ACCOUNT_TYPES = {
    TelegramAccount.AccountType.PERSONAL,
    # Customers writing to the connected Telegram bot are ordinary private
    # conversations. ``Chat.is_bot`` still excludes dialogs where the peer is a bot.
    TelegramAccount.AccountType.BOT,
    TelegramAccount.AccountType.WHATSAPP,
    TelegramAccount.AccountType.MAX,
}

def _visible_chat(chat: Chat) -> bool:
    return bool(
        chat.chat_type == Chat.ChatType.PRIVATE
        and not chat.is_bot
        and chat.telegram_account.account_type in SUPPORTED_ACCOUNT_TYPES
    )


def operator_absent_since(config: AISettings):
    """Return when the last active CRM session became inactive or timed out."""
    inactive_since = OperatorPresenceSession.objects.exclude(inactive_since=None).order_by(
        '-inactive_since',
    ).values_list('inactive_since', flat=True).first()
    last_active = OperatorPresenceSession.objects.filter(
        is_visible=True,
        last_active_at__isnull=False,
    ).order_by('-last_active_at').values_list('last_active_at', flat=True).first()
    timed_out_at = None
    if last_active:
        timed_out_at = last_active + timedelta(seconds=max(15, config.presence_timeout_seconds))
    candidates = [value for value in (inactive_since, timed_out_at) if value is not None]
    return max(candidates) if candidates else None


def operator_is_present(config: AISettings | None = None) -> bool:
    config = config or AISettings.load()
    cutoff = timezone.now() - timedelta(seconds=max(15, config.presence_timeout_seconds))
    return OperatorPresenceSession.objects.filter(
        is_visible=True,
        last_active_at__gte=cutoff,
    ).exists()


def _message_is_fresh(message: Message, config: AISettings) -> bool:
    event_time = message.telegram_date or message.created_at
    if timezone.is_naive(event_time):
        event_time = timezone.make_aware(event_time)
    cutoff = timezone.now() - timedelta(minutes=max(1, config.max_incoming_age_minutes))
    return event_time >= cutoff


def register_incoming_message(message_id: int) -> None:
    """Create or replace the one pending AI reply for a live incoming message."""
    message = Message.objects.select_related('chat__telegram_account').filter(pk=message_id).first()
    if not message or message.is_outgoing or not _visible_chat(message.chat):
        return
    if message.chat.ai_disabled:
        return
    if message.chat.ai_paused_until and message.chat.ai_paused_until > timezone.now():
        return

    config = AISettings.objects.filter(pk=1, enabled=True).first()
    if not config or not config.is_active():
        return
    if not _message_is_fresh(message, config):
        logger.info('Ignored stale incoming message %s for AI auto-reply', message.id)
        return

    present = operator_is_present(config)
    delay = config.online_delay_seconds if present and config.online_override_enabled else config.offline_delay_seconds
    due_at = timezone.now() + timedelta(seconds=max(1, delay))
    with transaction.atomic():
        state, _ = ChatAIState.objects.select_for_update().get_or_create(chat=message.chat)
        state.source_message = message
        state.due_at = due_at
        state.generation += 1
        state.processing = False
        state.last_error = ''
        state.save()
        generation = state.generation

    from ..tasks import process_ai_reply_task

    transaction.on_commit(
        lambda: process_ai_reply_task.apply_async(
            args=[message.chat_id, generation],
            countdown=max(1, delay),
        )
    )


def register_manual_outgoing(message_id: int) -> None:
    """A human reply cancels pending AI work and owns the chat for one hour."""
    message = Message.objects.select_related('chat__telegram_account').filter(pk=message_id).first()
    if not message or not message.is_outgoing or not _visible_chat(message.chat):
        return
    pause_chat_for_operator(message.chat_id)


def pause_chat_for_operator(chat_id: int) -> None:
    config = AISettings.objects.filter(pk=1, enabled=True).first()
    if not config:
        return
    paused_until = timezone.now() + timedelta(minutes=max(1, config.manual_pause_minutes))
    chat = Chat.objects.filter(pk=chat_id).first()
    if not chat:
        return
    Chat.objects.filter(pk=chat_id).update(
        ai_paused_until=paused_until,
        needs_human_attention=False,
    )
    with transaction.atomic():
        state, _ = ChatAIState.objects.select_for_update().get_or_create(chat=chat)
        state.generation += 1
        state.source_message = None
        state.due_at = None
        state.processing = False
        state.last_error = ''
        state.save()


def register_provider_outgoing(message_id: int, api_message: bool = False) -> None:
    """Treat phone/desktop messages as human, while ignoring our own AI webhook echo."""
    message = Message.objects.filter(pk=message_id).first()
    if not message:
        return
    provider_id = str(message.external_message_id or message.telegram_id or '')
    delivery = None
    if provider_id:
        delivery = OutboundDelivery.objects.filter(
            chat_id=message.chat_id,
            provider_message_id=provider_id,
        ).order_by('-updated_at').first()
    # A webhook can arrive before the provider call returns its message ID. Match
    # only the same recent text; an unrelated phone/desktop message must never be
    # mistaken for the pending AI delivery merely because both share a chat.
    if not delivery and (message.text or '').strip():
        delivery = OutboundDelivery.objects.filter(
            chat_id=message.chat_id,
            origin=OutboundDelivery.Origin.AI,
            status__in=[OutboundDelivery.Status.PROCESSING, OutboundDelivery.Status.RETRY],
            text=message.text,
            updated_at__gte=timezone.now() - timedelta(minutes=5),
        ).order_by('-updated_at').first()
    if not delivery or delivery.origin != OutboundDelivery.Origin.AI:
        register_manual_outgoing(message_id)


def _context_messages(chat: Chat, config: AISettings) -> list[dict]:
    records = list(
        Message.objects.filter(chat=chat)
        .exclude(message_type=Message.MessageType.SERVICE)
        .only('text', 'media_caption', 'message_type', 'is_outgoing', 'telegram_date')
        .order_by('-telegram_date', '-id')[: max(4, min(config.context_message_limit, 50))]
    )
    records.reverse()
    result: list[dict] = []
    used = 0
    for record in reversed(records):
        text = (record.text or record.media_caption or '').strip()
        if not text:
            text = f'[{record.get_message_type_display()}]'
        remaining = max(0, config.context_character_limit - used)
        if not remaining:
            break
        text = text[-remaining:]
        used += len(text)
        result.append({'role': 'assistant' if record.is_outgoing else 'user', 'content': text})
    result.reverse()
    return result


def _extract_json(content: str) -> dict:
    raw = (content or '').strip()
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find('{'), raw.rfind('}')
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('AI response is not an object')
    return value


def request_ai_decision(chat: Chat, config: AISettings) -> tuple[str | None, bool]:
    """Return (reply text, needs human); ``None`` means the dialog needs no reply."""
    api_key = getattr(settings, 'VSEGPT_API_KEY', '')
    if not api_key:
        raise RuntimeError('VSEGPT_API_KEY is not configured')
    from django.core.cache import cache
    if not cache.add('crm:ai:vsegpt-window', '1', timeout=3):
        raise RuntimeError('VSEGPT_RATE_LIMIT')

    system_prompt = (
        'ОБЯЗАТЕЛЬНАЯ ЛОГИКА АВТООТВЕТЧИКА:\n'
        'Для приветствий, обычного разговора, вежливых фраз и общих вопросов используй свои обычные знания '
        'и выбирай answer. Не отправляй собеседника администратору только из-за того, что сообщение не относится '
        'к компании. На приветствие всегда отвечай кратко и доброжелательно.\n'
        'Если вопрос непосредственно касается этой организации, её услуг, цен, расписания, правил или других '
        'фактов о ней, отвечай только на основании информации о компании. Если нужного факта там нет, '
        'выбирай handoff и ничего не выдумывай.\n'
        'Выбирай no_reply только для явно завершающих разговор коротких сообщений без вопроса или новой просьбы: '
        'благодарность, подтверждение вроде «хорошо/понял», прощание.\n\n'
        f'ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ АДМИНИСТРАТОРА:\n{config.base_prompt.strip()}\n\n'
        'ИНФОРМАЦИЯ О КОМПАНИИ (единственный допустимый источник фактов о ней):\n'
        f'<company_information>\n{config.company_information.strip()}\n</company_information>\n\n'
        'Верни только JSON без Markdown: '
        '{"action":"answer", "handoff" или "no_reply","answer":"текст ответа"}. '
        'Отвечай только по существу вопроса, не пересказывай всю информацию компании.'
    )
    response = requests.post(
        'https://api.vsegpt.ru/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'X-Title': 'Omnichannel CRM AI assistant',
        },
        json={
            'model': config.model,
            'messages': [{'role': 'system', 'content': system_prompt}, *_context_messages(chat, config)],
            'temperature': 0.1,
            'max_tokens': max(64, min(config.max_response_tokens, 1000)),
            # VseGPT's provider for gpt-4o-mini supports the OpenAI-compatible
            # json_object mode. Its non-standard json_output mode does not.
            'response_format': {'type': 'json_object'},
        },
        timeout=(5, 35),
    )
    if response.status_code == 429:
        raise RuntimeError('VSEGPT_RATE_LIMIT')
    if response.status_code >= 400:
        try:
            error = response.json().get('error', {})
            detail = error.get('message') if isinstance(error, dict) else error
        except (ValueError, AttributeError):
            detail = response.text[:500]
        raise RuntimeError(f'VSEGPT_HTTP_{response.status_code}: {detail or "unknown error"}')
    try:
        payload = response.json()
        content = payload['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('VSEGPT_INVALID_RESPONSE') from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('VSEGPT_EMPTY_RESPONSE')
    try:
        decision = _extract_json(content)
    except (ValueError, TypeError, json.JSONDecodeError):
        # Some upstream routes occasionally ignore JSON mode and return the
        # useful final answer as plain text. Do not replace that valid answer
        # with a misleading handoff message.
        logger.warning('VseGPT returned plain text instead of JSON; accepting it: %r', content[:500])
        return content.strip()[:4000], False
    action = str(decision.get('action') or '').lower()
    answer = str(decision.get('answer') or '').strip()
    if action == 'no_reply':
        return None, False
    if action != 'answer' or not answer:
        return config.fallback_text, True
    return answer[:4000], False


def next_eligible_delay(chat: Chat, config: AISettings) -> int:
    now = timezone.now()
    if chat.ai_paused_until and chat.ai_paused_until > now:
        return max(1, int((chat.ai_paused_until - now).total_seconds()))
    if operator_is_present(config):
        if not config.online_override_enabled:
            return max(15, config.presence_timeout_seconds)
        return 0
    absent_at = operator_absent_since(config)
    if absent_at:
        due = absent_at + timedelta(seconds=max(1, config.offline_delay_seconds))
        if due > now:
            return max(1, int((due - now).total_seconds()))
    return 0


def process_ai_reply(chat_id: int, generation: int) -> str:
    """Process one generation. The caller handles retries/rescheduling."""
    config = AISettings.load()
    if not config.is_active():
        return 'disabled'
    state = ChatAIState.objects.select_related(
        'chat__telegram_account', 'source_message',
    ).filter(chat_id=chat_id).first()
    if not state or state.generation != generation or not state.source_message_id:
        return 'stale'
    chat = state.chat
    if not _visible_chat(chat):
        return 'ineligible'
    if chat.ai_disabled:
        return 'chat-disabled'
    if not _message_is_fresh(state.source_message, config):
        ChatAIState.objects.filter(
            pk=state.pk,
            generation=generation,
            source_message_id=state.source_message_id,
        ).update(source_message=None, due_at=None, processing=False)
        return 'expired-message'

    latest = Message.objects.filter(chat=chat).order_by('-telegram_date', '-id').first()
    if not latest or latest.is_outgoing or latest.id != state.source_message_id:
        return 'already-answered'
    if OutboundDelivery.objects.filter(
        chat=chat,
        origin=OutboundDelivery.Origin.AI,
        status__in=[OutboundDelivery.Status.PENDING, OutboundDelivery.Status.PROCESSING, OutboundDelivery.Status.RETRY],
    ).exists():
        return 'already-queued'

    delay = next_eligible_delay(chat, config)
    if delay:
        return f'reschedule:{delay}'

    with transaction.atomic():
        claimed = ChatAIState.objects.select_for_update().get(pk=state.pk)
        if claimed.generation != generation or not claimed.source_message_id:
            return 'stale'
        if claimed.processing:
            return 'already-processing'
        claimed.processing = True
        claimed.save(update_fields=['processing', 'updated_at'])

    try:
        if latest.message_type != Message.MessageType.TEXT or not (latest.text or '').strip():
            answer, needs_human = config.fallback_text, True
        else:
            answer, needs_human = request_ai_decision(chat, config)

        # Recheck under a row lock immediately before creating the durable delivery.
        with transaction.atomic():
            locked = ChatAIState.objects.select_for_update().select_related('chat').get(pk=state.pk)
            current_config = AISettings.objects.filter(pk=1, enabled=True).first()
            if not current_config or not current_config.is_active():
                return 'disabled'
            if locked.chat.ai_disabled:
                return 'chat-disabled'
            runtime_delay = next_eligible_delay(locked.chat, current_config)
            if runtime_delay:
                return f'reschedule:{runtime_delay}'
            current_latest = Message.objects.filter(chat_id=chat_id).order_by('-telegram_date', '-id').first()
            if (
                locked.generation != generation
                or not current_latest
                or current_latest.is_outgoing
                or current_latest.id != locked.source_message_id
            ):
                return 'cancelled'
            if answer is None:
                locked.replied_to_message = current_latest
                locked.source_message = None
                locked.due_at = None
                locked.processing = False
                locked.last_error = ''
                locked.save()
                return 'no-reply'
            from .outbound_delivery import enqueue_delivery

            delivery = enqueue_delivery(
                chat=chat,
                text=answer,
                origin=OutboundDelivery.Origin.AI,
            )
            locked.replied_to_message = current_latest
            locked.source_message = None
            locked.due_at = None
            locked.processing = False
            locked.last_error = ''
            locked.save()
            if needs_human:
                Chat.objects.filter(pk=chat_id).update(needs_human_attention=True)
            logger.info('Queued AI delivery %s for chat %s', delivery.id, chat_id)
        return 'queued'
    finally:
        # Also releases the claim after provider/API errors so the bounded retry can run.
        ChatAIState.objects.filter(
            pk=state.pk,
            generation=generation,
            processing=True,
        ).update(processing=False)


def queue_failure_fallback(chat_id: int, generation: int, error: str) -> str:
    """Fail closed: notify the client once and flag the conversation for staff."""
    config = AISettings.load()
    if not config.is_active():
        return 'disabled'
    with transaction.atomic():
        state = ChatAIState.objects.select_for_update().select_related('chat').filter(chat_id=chat_id).first()
        if not state or state.generation != generation or not state.source_message_id:
            return 'stale'
        latest = Message.objects.filter(chat_id=chat_id).order_by('-telegram_date', '-id').first()
        if not latest or latest.is_outgoing or latest.id != state.source_message_id:
            return 'cancelled'
        from .outbound_delivery import enqueue_delivery

        enqueue_delivery(
            chat=state.chat,
            text=config.fallback_text,
            origin=OutboundDelivery.Origin.AI,
        )
        state.replied_to_message = latest
        state.source_message = None
        state.due_at = None
        state.processing = False
        state.last_error = error[:2000]
        state.save()
        Chat.objects.filter(pk=chat_id).update(needs_human_attention=True)
    return 'fallback-queued'
