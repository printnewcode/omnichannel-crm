"""Normalize provider-specific message payloads for storage and presentation."""

from __future__ import annotations

from typing import Any


GREEN_MESSAGE_TYPES = {
    'textMessage': 'text',
    'extendedTextMessage': 'text',
    'quotedMessage': 'text',
    'imageMessage': 'photo',
    'videoMessage': 'video',
    'audioMessage': 'voice',
    'documentMessage': 'document',
    'stickerMessage': 'sticker',
    'locationMessage': 'location',
    'contactMessage': 'contact',
    'contactsArrayMessage': 'contact',
    'pollMessage': 'poll',
    'pollUpdateMessage': 'poll',
    'groupInviteMessage': 'other',
    'reactionMessage': 'other',
    'buttonsResponseMessage': 'text',
    'templateButtonReplyMessage': 'text',
    'listResponseMessage': 'text',
    'editedMessage': 'text',
    'deletedMessage': 'service',
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get('text') or value.get('value') or '')
    if value is not None and hasattr(value, 'text'):
        return str(getattr(value, 'text') or '')
    return str(value or '')


def _contact_item(value: Any) -> dict:
    data = _dict(value)
    name = data.get('displayName') or data.get('name')
    if not name:
        name = ' '.join(filter(None, [data.get('firstName'), data.get('lastName')]))
    phone = data.get('phoneNumber') or data.get('phone') or ''
    vcard = str(data.get('vcard') or '')
    if not phone and vcard:
        for line in vcard.splitlines():
            if line.upper().startswith('TEL') and ':' in line:
                phone = line.split(':', 1)[1].strip()
                break
    return {'name': str(name or 'Контакт'), 'phone': str(phone or ''), 'vcard': vcard}


def _forward_name(value: Any) -> str:
    data = _dict(value)
    return str(
        data.get('forwardedFromName') or data.get('forwardFromName')
        or data.get('forwardSenderName') or data.get('forwardedFrom') or ''
    ).strip()


def _green_forward_info(root: dict, data: dict, content: dict) -> dict | None:
    candidates = [content, data, root]
    def is_forwarded(item):
        try:
            score = int(item.get('forwardingScore') or 0)
        except (TypeError, ValueError):
            score = 0
        return item.get('isForwarded') is True or score > 0

    forwarded = any(is_forwarded(item) for item in candidates)
    if not forwarded:
        return None
    name = next((_forward_name(item) for item in candidates if _forward_name(item)), '')
    return {'is_forwarded': True, 'from_name': name or None}


def _green_special(raw_type: str, content: dict) -> dict | None:
    if raw_type == 'deletedMessage':
        return {'kind': 'service', 'label': 'Сообщение удалено'}
    if raw_type == 'locationMessage':
        return {
            'kind': 'location',
            'latitude': content.get('latitude'),
            'longitude': content.get('longitude'),
            'name': str(content.get('nameLocation') or content.get('name') or ''),
            'address': str(content.get('address') or ''),
        }
    if raw_type in {'contactMessage', 'contactsArrayMessage'}:
        raw_contacts = content.get('contacts') or content.get('contactsArray') or content.get('items')
        contacts = [_contact_item(item) for item in _list(raw_contacts)]
        if not contacts:
            contacts = [_contact_item(content)]
        return {'kind': 'contact', 'contacts': contacts}
    if raw_type in {'pollMessage', 'pollUpdateMessage'}:
        options = []
        for option in _list(content.get('options') or content.get('votes')):
            data = _dict(option)
            label = data.get('optionName') or data.get('name') or data.get('text')
            if label:
                options.append(str(label))
        return {
            'kind': 'poll',
            'question': str(content.get('name') or content.get('question') or 'Опрос'),
            'options': options,
            'is_update': raw_type == 'pollUpdateMessage',
        }
    if raw_type == 'groupInviteMessage':
        return {
            'kind': 'group_invite',
            'title': str(content.get('groupName') or content.get('caption') or 'Приглашение в группу'),
            'url': str(content.get('inviteLink') or content.get('url') or ''),
        }
    if raw_type == 'reactionMessage':
        return {'kind': 'reaction', 'emoji': str(content.get('text') or content.get('emoji') or '')}
    return None


def normalize_green_message(payload: Any) -> dict:
    """Support both webhook ``messageData`` and flattened history entries."""
    root = _dict(payload)
    data = _dict(root.get('messageData')) or root
    raw_type = str(data.get('typeMessage') or root.get('typeMessage') or 'unknown')
    if data.get('isDeleted') or root.get('isDeleted') or raw_type == 'deletedMessage':
        deleted = _dict(data.get('deletedMessageData')) or data
        return {
            'raw_type': raw_type,
            'message_type': 'service',
            'text': '',
            'content': deleted,
            'special_content': {'kind': 'service', 'label': 'Сообщение удалено'},
            'download_url': None,
            'forward_info': None,
        }
    text_data = _dict(data.get('textMessageData'))
    extended = _dict(data.get('extendedTextMessageData')) or _dict(data.get('extendedTextMessage'))
    file_data = _dict(data.get('fileMessageData')) or _dict(data.get('stickerMessageData'))

    if raw_type == 'editedMessage':
        content = _dict(data.get('editedMessageData')) or data
    elif raw_type == 'locationMessage':
        content = _dict(data.get('locationMessageData')) or _dict(data.get('location')) or data
    elif raw_type == 'contactMessage':
        content = _dict(data.get('contactMessageData')) or _dict(data.get('contact')) or data
    elif raw_type == 'contactsArrayMessage':
        content = _dict(data.get('contactsArrayMessageData')) or {
            'contacts': data.get('contacts') or data.get('contactsArray') or []
        }
    elif raw_type in {'pollMessage', 'pollUpdateMessage'}:
        content = _dict(data.get('pollMessageData')) or _dict(data.get('pollData')) or data
    elif raw_type == 'groupInviteMessage':
        content = _dict(data.get('groupInviteMessageData')) or data
    elif raw_type == 'reactionMessage':
        content = _dict(data.get('reactionMessageData')) or extended or data
    elif raw_type in {'imageMessage', 'videoMessage', 'audioMessage', 'documentMessage', 'stickerMessage'}:
        content = file_data or data
    elif raw_type in {'extendedTextMessage', 'quotedMessage'}:
        content = extended or data
    elif raw_type == 'textMessage':
        content = text_data or data
    elif raw_type in {'buttonsResponseMessage', 'templateButtonReplyMessage', 'listResponseMessage'}:
        content = (
            _dict(data.get('buttonsResponseMessageData'))
            or _dict(data.get('templateButtonReplyMessage'))
            or _dict(data.get('listResponseMessageData'))
            or data
        )
    else:
        content = data

    text = (
        data.get('textMessage') or text_data.get('textMessage')
        or content.get('textMessage')
        or extended.get('text') or extended.get('textMessage')
        or content.get('caption') or content.get('text') or content.get('body')
        or content.get('selectedDisplayText')
        or content.get('selectedButtonId') or content.get('title') or ''
    )
    special = _green_special(raw_type, content)
    if not text and special:
        if special['kind'] == 'contact':
            text = ', '.join(item['name'] for item in special['contacts'])
        elif special['kind'] == 'poll':
            text = special['question']
        elif special['kind'] == 'group_invite':
            text = special['title']
    download_url = (
        data.get('downloadUrl') or data.get('downloadUrlJpeg')
        or content.get('downloadUrl') or content.get('downloadUrlJpeg')
    )
    return {
        'raw_type': raw_type,
        'message_type': GREEN_MESSAGE_TYPES.get(raw_type, 'other'),
        'text': str(text or ''),
        'content': content,
        'special_content': special,
        'download_url': download_url,
        'forward_info': _green_forward_info(root, data, content),
    }


def telegram_forward_info(message: Any) -> dict | None:
    """Return the visible Telegram forward origin when Telethon exposes it."""
    forward = getattr(message, 'forward', None) or getattr(message, 'fwd_from', None)
    if not forward:
        return None
    sender = getattr(forward, 'sender', None)
    sender_name = ' '.join(filter(None, [
        getattr(sender, 'first_name', None), getattr(sender, 'last_name', None),
    ]))
    name = (
        getattr(forward, 'from_name', None)
        or getattr(forward, 'post_author', None)
        or sender_name
        or getattr(sender, 'title', None)
        or getattr(sender, 'username', None)
        or ''
    )
    return {'is_forwarded': True, 'from_name': str(name).strip() or None}


def telegram_special_content(message: Any) -> dict | None:
    venue = getattr(message, 'venue', None)
    geo = getattr(message, 'geo', None)
    if venue or geo:
        point = getattr(venue, 'geo', None) or geo
        return {
            'kind': 'location', 'latitude': getattr(point, 'lat', None),
            'longitude': getattr(point, 'long', None),
            'name': str(getattr(venue, 'title', '') or ''),
            'address': str(getattr(venue, 'address', '') or ''),
        }
    contact = getattr(message, 'contact', None)
    if contact:
        return {'kind': 'contact', 'contacts': [{
            'name': ' '.join(filter(None, [getattr(contact, 'first_name', None), getattr(contact, 'last_name', None)])) or 'Контакт',
            'phone': str(getattr(contact, 'phone_number', '') or ''),
            'vcard': str(getattr(contact, 'vcard', '') or ''),
        }]}
    poll_media = getattr(message, 'poll', None)
    if poll_media:
        poll = getattr(poll_media, 'poll', None) or poll_media
        options = [_text(getattr(answer, 'text', None)) for answer in getattr(poll, 'answers', [])]
        return {
            'kind': 'poll', 'question': _text(getattr(poll, 'question', None)) or 'Опрос',
            'options': [item for item in options if item],
        }
    media = getattr(message, 'media', None)
    if media and media.__class__.__name__ == 'MessageMediaDice':
        return {'kind': 'dice', 'emoji': str(getattr(media, 'emoticon', '') or '🎲'), 'value': getattr(media, 'value', None)}
    action = getattr(message, 'action', None)
    if action:
        return {'kind': 'service', 'label': str(getattr(action, 'message', None) or action.__class__.__name__)}
    return None


def special_content_from_metadata(message_type: str, metadata: Any) -> dict | None:
    """Build UI content for new records and legacy records saved before normalization."""
    source = _dict(metadata)
    special = source.get('special_content')
    if isinstance(special, dict) and special.get('kind'):
        return special
    raw_type = str(source.get('raw_type') or '')
    provider_content = _dict(source.get('provider_content'))
    if raw_type:
        normalized = normalize_green_message({**provider_content, 'typeMessage': raw_type})
        if normalized['special_content']:
            return normalized['special_content']
    if message_type == 'location':
        return {'kind': 'location', 'latitude': None, 'longitude': None, 'name': '', 'address': ''}
    if message_type == 'contact':
        return {'kind': 'contact', 'contacts': []}
    if message_type == 'poll':
        return {'kind': 'poll', 'question': 'Опрос', 'options': []}
    if message_type in {'other', 'service'}:
        return {'kind': 'unsupported', 'label': 'Неподдерживаемое сообщение'}
    return None


def forward_info_from_metadata(metadata: Any) -> dict | None:
    source = _dict(metadata)
    stored = source.get('forward_info')
    if isinstance(stored, dict) and stored.get('is_forwarded'):
        return {
            'is_forwarded': True,
            'from_name': str(stored.get('from_name') or '').strip() or None,
        }
    raw_type = str(source.get('raw_type') or '')
    provider_content = _dict(source.get('provider_content'))
    if raw_type and provider_content:
        return normalize_green_message({**provider_content, 'typeMessage': raw_type})['forward_info']
    return None
