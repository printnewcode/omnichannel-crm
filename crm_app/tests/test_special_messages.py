import json
from types import SimpleNamespace

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, Message, TelegramAccount
from crm_app.serializers import MessageSerializer
from crm_app.services.history_import import _ingest_green_items
from crm_app.services.message_content import (
    normalize_green_message,
    telegram_forward_info,
    telegram_special_content,
)
from crm_app.services.telegram_client_manager import TelegramClientManager


class GreenSpecialMessageTests(TestCase):
    def setUp(self):
        self.account = TelegramAccount.objects.create(
            name='MAX special', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='77', green_api_token='token',
        )
        self.chat = Chat.objects.create(
            telegram_id=16101503, telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE, title='Special messages',
            metadata={'external_chat_id': '16101503'},
        )

    def test_flat_history_types_are_normalized(self):
        items = [
            {
                'idMessage': 'location', 'type': 'outgoing', 'timestamp': 1773548391,
                'typeMessage': 'locationMessage',
                'location': {'latitude': 55.6767, 'longitude': 50.6315, 'nameLocation': 'Офис'},
            },
            {
                'idMessage': 'contact', 'type': 'incoming', 'timestamp': 1773548392,
                'typeMessage': 'contactMessage',
                'contact': {'displayName': 'Иван', 'vcard': 'BEGIN:VCARD\nTEL:+79990000000\nEND:VCARD'},
            },
            {
                'idMessage': 'contacts', 'type': 'incoming', 'timestamp': 1773548393,
                'typeMessage': 'contactsArrayMessage',
                'contacts': [{'displayName': 'Анна'}, {'displayName': 'Пётр'}],
            },
            {
                'idMessage': 'poll', 'type': 'incoming', 'timestamp': 1773548394,
                'typeMessage': 'pollMessage',
                'pollMessageData': {'name': 'Выберите время', 'options': [
                    {'optionName': 'Утро'}, {'optionName': 'Вечер'},
                ]},
            },
            {
                'idMessage': 'invite', 'type': 'incoming', 'timestamp': 1773548395,
                'typeMessage': 'groupInviteMessage',
                'groupInviteMessageData': {'groupName': 'Команда', 'inviteLink': 'https://example.org/invite'},
            },
        ]

        created, processed, _ = _ingest_green_items(self.chat, list(reversed(items)))

        self.assertEqual((created, processed), (5, 5))
        values = {message.external_message_id: message for message in Message.objects.all()}
        self.assertEqual(values['location'].message_type, Message.MessageType.LOCATION)
        self.assertEqual(values['contact'].message_type, Message.MessageType.CONTACT)
        self.assertEqual(values['contacts'].metadata['special_content']['contacts'][1]['name'], 'Пётр')
        self.assertEqual(values['poll'].message_type, Message.MessageType.POLL)
        self.assertEqual(values['poll'].metadata['special_content']['options'], ['Утро', 'Вечер'])
        self.assertEqual(values['invite'].metadata['special_content']['kind'], 'group_invite')

    def test_legacy_empty_location_is_presented_without_database_rewrite(self):
        message = Message.objects.create(
            chat=self.chat, external_message_id='legacy-location', message_type=Message.MessageType.TEXT,
            text=None, is_outgoing=True, telegram_date=timezone.now(),
            metadata={
                'raw_type': 'locationMessage',
                'provider_content': {
                    'typeMessage': 'locationMessage',
                    'location': {'latitude': 55.6767, 'longitude': 50.6315},
                },
            },
        )

        data = MessageSerializer(message).data

        self.assertEqual(data['special_content']['kind'], 'location')
        self.assertEqual(data['special_content']['latitude'], 55.6767)
        self.assertIsNone(message.text)

    def test_nested_webhook_location_and_interactive_reply(self):
        location = normalize_green_message({
            'typeMessage': 'locationMessage',
            'locationMessageData': {'latitude': 51.1, 'longitude': 71.4, 'address': 'Астана'},
        })
        button = normalize_green_message({
            'typeMessage': 'buttonsResponseMessage',
            'buttonsResponseMessageData': {'selectedDisplayText': 'Подтверждаю'},
        })
        self.assertEqual(location['message_type'], Message.MessageType.LOCATION)
        self.assertEqual(location['special_content']['address'], 'Астана')
        self.assertEqual(button['message_type'], Message.MessageType.TEXT)
        self.assertEqual(button['text'], 'Подтверждаю')

    def test_edited_message_is_normalized_to_new_text(self):
        normalized = normalize_green_message({
            'typeMessage': 'editedMessage',
            'editedMessageData': {
                'textMessage': 'Исправленный текст',
                'stanzaId': 'original-message',
            },
        })

        self.assertEqual(normalized['message_type'], Message.MessageType.TEXT)
        self.assertEqual(normalized['text'], 'Исправленный текст')
        self.assertEqual(normalized['content']['stanzaId'], 'original-message')

    def test_green_forward_is_exposed_even_without_origin_name(self):
        normalized = normalize_green_message({
            'typeMessage': 'extendedTextMessage',
            'extendedTextMessageData': {
                'text': 'Forwarded text',
                'isForwarded': True,
                'forwardingScore': 2,
            },
        })

        self.assertEqual(normalized['forward_info'], {
            'is_forwarded': True,
            'from_name': None,
        })

    def test_legacy_green_forward_is_exposed_by_serializer(self):
        message = Message.objects.create(
            chat=self.chat, external_message_id='legacy-forward',
            message_type=Message.MessageType.TEXT, text='Forwarded text',
            telegram_date=timezone.now(),
            metadata={
                'raw_type': 'extendedTextMessage',
                'provider_content': {
                    'text': 'Forwarded text',
                    'isForwarded': True,
                    'forwardingScore': 1,
                },
            },
        )

        self.assertEqual(MessageSerializer(message).data['forward_info'], {
            'is_forwarded': True,
            'from_name': None,
        })

    def test_future_webhook_message_is_stored_with_ui_content(self):
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 77},
            'timestamp': 1773548391,
            'idMessage': 'live-location',
            'senderData': {
                'chatId': '16101503', 'sender': '16101503',
                'senderName': 'Клиент', 'chatType': 'user',
            },
            'messageData': {
                'typeMessage': 'locationMessage',
                'locationMessageData': {'latitude': 55.75, 'longitude': 37.61, 'address': 'Москва'},
            },
        }

        response = self.client.post(
            reverse('max-webhook', kwargs={'account_id': self.account.id}),
            data=json.dumps(payload), content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='live-location')
        self.assertEqual(message.message_type, Message.MessageType.LOCATION)
        self.assertEqual(MessageSerializer(message).data['special_content']['address'], 'Москва')

    def test_unknown_type_gets_visible_fallback(self):
        message = Message.objects.create(
            chat=self.chat, external_message_id='future-type', message_type=Message.MessageType.OTHER,
            telegram_date=timezone.now(), metadata={'raw_type': 'futureMagicMessage'},
        )
        data = MessageSerializer(message).data
        self.assertEqual(data['special_content']['kind'], 'unsupported')
        self.assertEqual(data['special_content']['label'], 'Неподдерживаемое сообщение')

    def test_legacy_edited_message_exposes_text_instead_of_provider_type(self):
        message = Message.objects.create(
            chat=self.chat, external_message_id='legacy-edit-event',
            message_type=Message.MessageType.OTHER, telegram_date=timezone.now(),
            metadata={
                'raw_type': 'editedMessage',
                'provider_content': {
                    'textMessage': 'Исправленный старый текст',
                    'stanzaId': 'legacy-original',
                },
            },
        )

        data = MessageSerializer(message).data

        self.assertEqual(data['message_type'], Message.MessageType.TEXT)
        self.assertEqual(data['text'], 'Исправленный старый текст')
        self.assertIsNone(data['special_content'])

    def test_live_edited_message_updates_original_without_extra_bubble(self):
        original = Message.objects.create(
            chat=self.chat, external_message_id='original-message', text='Старый текст',
            message_type=Message.MessageType.TEXT, telegram_date=timezone.now(),
        )
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 77},
            'timestamp': 1773548400,
            'idMessage': 'edit-event',
            'senderData': {
                'chatId': '16101503', 'sender': '16101503',
                'senderName': 'Клиент', 'chatType': 'user',
            },
            'messageData': {
                'typeMessage': 'editedMessage',
                'editedMessageData': {
                    'textMessage': 'Новый текст',
                    'stanzaId': 'original-message',
                },
            },
        }

        response = self.client.post(
            reverse('max-webhook', kwargs={'account_id': self.account.id}),
            data=json.dumps(payload), content_type='application/json',
        )

        original.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(original.text, 'Новый текст')
        self.assertEqual(original.message_type, Message.MessageType.TEXT)
        self.assertFalse(Message.objects.filter(external_message_id='edit-event').exists())

    def test_history_reaction_updates_original_message_without_empty_bubble(self):
        target = Message.objects.create(
            chat=self.chat, external_message_id='target', text='Сообщение',
            telegram_date=timezone.now(),
        )
        reaction = [{
            'idMessage': 'reaction-event', 'type': 'incoming', 'timestamp': 1773548396,
            'typeMessage': 'reactionMessage',
            'extendedTextMessageData': {'text': '👍'},
            'quotedMessage': {'stanzaId': 'target'},
            'senderId': '16101503',
        }]

        created, processed, _ = _ingest_green_items(self.chat, reaction)

        target.refresh_from_db()
        self.assertEqual((created, processed), (0, 1))
        self.assertFalse(Message.objects.filter(external_message_id='reaction-event').exists())
        self.assertEqual(target.metadata['reactions'], [{'emoji': '👍', 'count': 1, 'chosen': False}])


class TelegramSpecialMessageTests(TestCase):
    def test_telegram_forward_includes_origin_when_available(self):
        message = SimpleNamespace(
            forward=None,
            fwd_from=SimpleNamespace(
                from_name='Иван', post_author=None, sender=None,
            ),
        )

        self.assertEqual(telegram_forward_info(message), {
            'is_forwarded': True,
            'from_name': 'Иван',
        })

    def test_telegram_poll_contact_location_dice_and_service(self):
        manager = TelegramClientManager()
        poll = SimpleNamespace(
            photo=None, video=None, voice=None, audio=None, sticker=None, document=None,
            geo=None, contact=None, action=None,
            poll=SimpleNamespace(
                poll=SimpleNamespace(
                    question=SimpleNamespace(text='Ваш выбор?'),
                    answers=[SimpleNamespace(text=SimpleNamespace(text='Да'))],
                )
            ),
            media=None,
        )
        self.assertEqual(manager._get_message_type(poll), Message.MessageType.POLL)
        self.assertEqual(telegram_special_content(poll)['options'], ['Да'])

        location = SimpleNamespace(venue=None, geo=SimpleNamespace(lat=55.7, long=37.6), contact=None, poll=None, media=None, action=None)
        self.assertEqual(telegram_special_content(location)['kind'], 'location')

        contact = SimpleNamespace(
            venue=None, geo=None, poll=None, media=None, action=None,
            contact=SimpleNamespace(first_name='Иван', last_name='Иванов', phone_number='+7999', vcard=''),
        )
        self.assertEqual(telegram_special_content(contact)['contacts'][0]['phone'], '+7999')

        Dice = type('MessageMediaDice', (), {})
        dice_media = Dice()
        dice_media.emoticon = '🎲'
        dice_media.value = 6
        dice = SimpleNamespace(venue=None, geo=None, contact=None, poll=None, media=dice_media, action=None)
        self.assertEqual(telegram_special_content(dice)['value'], 6)

        action = SimpleNamespace(venue=None, geo=None, contact=None, poll=None, media=None, action=SimpleNamespace(message='Звонок завершён'))
        self.assertEqual(telegram_special_content(action)['kind'], 'service')
