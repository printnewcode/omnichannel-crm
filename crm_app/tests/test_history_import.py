from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crm_app.models import Chat, HistoryImportJob, Message, TelegramAccount
from crm_app.services.history_import import discover_green, import_green_history
from crm_app.services.whatsapp_client import GreenAPIClient


class HistoryImportApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('history-user', password='secret')
        self.client.force_login(self.user)
        self.account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='1', green_api_token='token',
        )
        self.chat = Chat.objects.create(
            telegram_id=79000000000, telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE, title='Client',
            metadata={'external_chat_id': '79000000000@c.us'},
        )

    @patch('crm_app.tasks.run_history_import.delay')
    def test_history_import_is_queued(self, delay):
        response = self.client.post(
            reverse('chat-import-history', kwargs={'pk': self.chat.pk}),
            {'count': 50}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        job = HistoryImportJob.objects.get()
        self.assertEqual(job.parameters['count'], 50)
        delay.assert_called_once_with(job.id)

    @patch('crm_app.tasks.run_history_import.delay')
    def test_chat_discovery_queues_active_provider_account(self, delay):
        response = self.client.post(
            reverse('telegram-account-import-chats'),
            {'messenger': 'whatsapp', 'since': '2026-06-13'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(response.json()['jobs']), 1)
        delay.assert_called_once()


class GreenHistoryImportTests(TestCase):
    def test_import_preserves_sticker_and_outgoing_direction(self):
        account = TelegramAccount.objects.create(
            name='MAX', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='2', green_api_token='token',
        )
        chat = Chat.objects.create(
            telegram_id=123, telegram_account=account, chat_type=Chat.ChatType.PRIVATE,
            metadata={'external_chat_id': '123'},
        )
        history = [{
            'idMessage': 'sticker-1', 'type': 'outgoing', 'timestamp': 1770000000,
            'typeMessage': 'stickerMessage', 'downloadUrl': 'https://media.green-api.com/sticker.webp',
            'fileName': 'sticker.webp',
        }]
        with patch.object(GreenAPIClient, 'get_chat_history', return_value=history):
            created, processed, media_ids = import_green_history(chat, 10)
        message = Message.objects.get()
        self.assertEqual((created, processed), (1, 1))
        self.assertTrue(message.is_outgoing)
        self.assertEqual(message.message_type, Message.MessageType.STICKER)
        self.assertEqual(media_ids, [message.id])

    @patch('crm_app.services.history_import.GreenAPIClient')
    def test_discovery_reuses_preview_and_skips_groups(self, client_class):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='4', green_api_token='token',
        )
        client = client_class.return_value
        client.get_chats.return_value = [
            {'id': '79000000001@c.us', 'type': 'user', 'name': 'Client'},
            {'id': '79000000001-1@g.us', 'type': 'group', 'name': 'Group'},
        ]
        client.get_chat_history.return_value = [{
            'idMessage': 'm1', 'type': 'incoming', 'timestamp': 1770000000,
            'typeMessage': 'textMessage', 'textMessage': 'Hello',
        }]

        discovered, imported, _ = discover_green(account, None, per_chat=5)

        self.assertEqual((discovered, imported), (1, 1))
        client.get_chat_history.assert_called_once_with('79000000001@c.us', count=5)
        self.assertFalse(Chat.objects.filter(title='Group').exists())


class GreenClientHistoryTests(TestCase):
    def test_history_and_chats_use_green_api_methods(self):
        account = TelegramAccount(
            account_type=TelegramAccount.AccountType.WHATSAPP,
            green_api_instance_id='3', green_api_token='token',
            green_api_url='https://api.green-api.com', green_media_url='https://media.green-api.com',
        )
        session = Mock()
        response = Mock(ok=True)
        response.json.side_effect = [[{'id': '1@c.us'}], [{'idMessage': 'm1'}]]
        session.request.return_value = response
        client = GreenAPIClient(account, session=session)
        self.assertEqual(client.get_chats(20)[0]['id'], '1@c.us')
        self.assertEqual(client.get_chat_history('1@c.us', 5)[0]['idMessage'], 'm1')
