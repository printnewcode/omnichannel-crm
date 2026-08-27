"""Read-only Google People API synchronization and local chat matching."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Chat, GoogleContact, GoogleContactsIntegration

logger = logging.getLogger(__name__)

GOOGLE_SCOPE = 'openid email https://www.googleapis.com/auth/contacts.readonly'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
PEOPLE_URL = 'https://people.googleapis.com/v1'


def normalize_phone(value) -> str:
    digits = re.sub(r'\D+', '', str(value or ''))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits


def phone_from_text(value) -> str:
    """Extract a phone-looking value from a human-visible chat name."""
    text = str(value or '')
    for candidate in re.findall(r'(?<!\d)(?:\+?\d[\d\s().-]{9,}\d)(?!\d)', text):
        normalized = normalize_phone(candidate)
        # Plain provider/user IDs are common in chat names. Requiring at least
        # 11 digits avoids treating most short Telegram/MAX IDs as phones.
        if 11 <= len(normalized) <= 15:
            return normalized
    return ''


def chat_phone(chat: Chat) -> str:
    metadata = chat.metadata if isinstance(chat.metadata, dict) else {}
    direct = normalize_phone(metadata.get('contact_phone') or metadata.get('sender_phone_number'))
    if direct:
        return direct
    external = str(metadata.get('external_chat_id') or '')
    if external.lower().endswith('@c.us'):
        return normalize_phone(external.split('@', 1)[0])
    for visible_name in (chat.title, chat.first_name):
        extracted = phone_from_text(visible_name)
        if extracted:
            return extracted
    return ''


def match_chat_contact(chat: Chat, save=True):
    phone = chat_phone(chat)
    if not phone:
        return None
    contact = GoogleContact.objects.filter(normalized_phone=phone, deleted=False).order_by('-updated_at').first()
    if save and chat.google_contact_id != getattr(contact, 'id', None):
        Chat.objects.filter(pk=chat.pk).update(google_contact=contact)
        chat.google_contact = contact
    return contact


def match_all_chats(batch_size=300) -> int:
    matched = 0
    queryset = Chat.objects.only(
        'id', 'metadata', 'title', 'first_name', 'google_contact_id',
    ).iterator(chunk_size=batch_size)
    for chat in queryset:
        if match_chat_contact(chat):
            matched += 1
    return matched


def authorization_url(*, redirect_uri: str, state: str) -> str:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise RuntimeError('GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not configured')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.GOOGLE_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': GOOGLE_SCOPE,
        'access_type': 'offline',
        'include_granted_scopes': 'true',
        'prompt': 'consent',
        'state': state,
    })


def exchange_code(integration: GoogleContactsIntegration, code: str, redirect_uri: str) -> None:
    response = requests.post(TOKEN_URL, data={
        'code': code,
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }, timeout=(5, 20))
    response.raise_for_status()
    payload = response.json()
    integration.access_token = payload.get('access_token', '')
    integration.refresh_token = payload.get('refresh_token') or integration.refresh_token
    integration.access_token_expires_at = timezone.now() + timedelta(seconds=max(60, int(payload.get('expires_in', 3600)) - 60))
    integration.last_error = ''
    try:
        profile = requests.get(
            'https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f"Bearer {integration.access_token}"},
            timeout=(5, 15),
        )
        if profile.ok:
            integration.account_email = str(profile.json().get('email') or '')
    except requests.RequestException:
        logger.info('Google account email was not available during authorization')
    integration.save()


def access_token(integration: GoogleContactsIntegration) -> str:
    if integration.access_token and integration.access_token_expires_at and integration.access_token_expires_at > timezone.now():
        return integration.access_token
    if not integration.refresh_token:
        raise RuntimeError('Google Contacts authorization is missing')
    response = requests.post(TOKEN_URL, data={
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'refresh_token': integration.refresh_token,
        'grant_type': 'refresh_token',
    }, timeout=(5, 20))
    response.raise_for_status()
    payload = response.json()
    integration.access_token = payload['access_token']
    integration.access_token_expires_at = timezone.now() + timedelta(seconds=max(60, int(payload.get('expires_in', 3600)) - 60))
    integration.save(update_fields=['access_token', 'access_token_expires_at', 'updated_at'])
    return integration.access_token


def _display_name(person: dict) -> str:
    names = person.get('names') or []
    if not names:
        return ''
    primary = next((item for item in names if (item.get('metadata') or {}).get('primary')), names[0])
    return str(primary.get('displayName') or '').strip()


def sync_contacts(integration_id: int) -> dict:
    integration = GoogleContactsIntegration.objects.get(pk=integration_id)
    token = access_token(integration)
    headers = {'Authorization': f'Bearer {token}'}
    params = {
        'personFields': 'metadata,names,phoneNumbers',
        'pageSize': 1000,
        'requestSyncToken': 'true',
    }
    incremental = bool(integration.sync_token)
    if incremental:
        params['syncToken'] = integration.sync_token

    people: list[dict] = []
    next_sync_token = ''
    while True:
        response = requests.get(
            f'{PEOPLE_URL}/people/me/connections',
            headers=headers,
            params=params,
            timeout=(5, 30),
        )
        if response.status_code == 410 and incremental:
            integration.sync_token = ''
            integration.save(update_fields=['sync_token', 'updated_at'])
            return sync_contacts(integration.id)
        response.raise_for_status()
        payload = response.json()
        people.extend(payload.get('connections') or [])
        next_page = payload.get('nextPageToken')
        if not next_page:
            next_sync_token = payload.get('nextSyncToken') or next_sync_token
            break
        params['pageToken'] = next_page

    created = updated = deleted = 0
    with transaction.atomic():
        if not incremental:
            GoogleContact.objects.filter(integration=integration).update(deleted=True)
        for person in people:
            resource_name = str(person.get('resourceName') or '')
            if not resource_name:
                continue
            if (person.get('metadata') or {}).get('deleted'):
                deleted += GoogleContact.objects.filter(
                    integration=integration, resource_name=resource_name, deleted=False,
                ).update(deleted=True)
                continue
            name = _display_name(person)
            GoogleContact.objects.filter(
                integration=integration,
                resource_name=resource_name,
            ).update(deleted=True)
            for phone_item in person.get('phoneNumbers') or []:
                raw_phone = phone_item.get('canonicalForm') or phone_item.get('value')
                normalized = normalize_phone(raw_phone)
                if not name or not normalized:
                    continue
                _, was_created = GoogleContact.objects.update_or_create(
                    integration=integration,
                    resource_name=resource_name,
                    normalized_phone=normalized,
                    defaults={
                        'display_name': name,
                        'phone_number': str(raw_phone),
                        'deleted': False,
                    },
                )
                created += int(was_created)
                updated += int(not was_created)
        integration.sync_token = next_sync_token or integration.sync_token
        integration.last_synced_at = timezone.now()
        integration.last_error = ''
        integration.save(update_fields=['sync_token', 'last_synced_at', 'last_error', 'updated_at'])

    matched = match_all_chats()
    return {
        'created': created,
        'updated': updated,
        'deleted': deleted,
        'matched_chats': matched,
        'incremental': incremental,
    }
