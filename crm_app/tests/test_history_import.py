from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, HistoryImportJob, Message, TelegramAccount
from crm_app.services.history_import import discover_green, import_green_history, import_telegram_history
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

    @patch('crm_app.tasks.run_history_import.delay')
    def test_duplicate_history_import_reuses_running_job(self, delay):
        existing = HistoryImportJob.objects.create(
            kind=HistoryImportJob.Kind.CHAT_HISTORY,
            status=HistoryImportJob.Status.RUNNING,
            account=self.account,
            chat=self.chat,
            requested_by=self.user,
        )

        response = self.client.post(
            reverse('chat-import-history', kwargs={'pk': self.chat.pk}),
            {'count': 50}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['id'], existing.id)
        self.assertEqual(HistoryImportJob.objects.count(), 1)
        delay.assert_not_called()

    @patch('crm_app.tasks.run_history_import.delay')
    def test_duplicate_chat_discovery_reuses_running_job(self, delay):
        existing = HistoryImportJob.objects.create(
            kind=HistoryImportJob.Kind.CHAT_DISCOVERY,
            status=HistoryImportJob.Status.PENDING,
            account=self.account,
            requested_by=self.user,
        )

        response = self.client.post(
            reverse('telegram-account-import-chats'),
            {'messenger': 'whatsapp'}, content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['jobs'][0]['id'], existing.id)
        self.assertEqual(HistoryImportJob.objects.count(), 1)
        delay.assert_not_called()


class TelegramHistoryAsyncBoundaryTests(TestCase):
    @patch('crm_app.services.history_import.asyncio.run')
    def test_related_account_is_resolved_before_asyncio_starts(self, run):
        account = TelegramAccount.objects.create(
            name='Telegram', account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
            api_id=123, api_hash='hash', session_string='session',
        )
        chat = Chat.objects.create(
            telegram_id=123, telegram_account=account,
            chat_type=Chat.ChatType.PRIVATE,
        )
        uncached_chat = Chat.objects.get(pk=chat.pk)
        run.return_value = (0, 0)

        self.assertEqual(import_telegram_history(uncached_chat, 10), (0, 0))
        coroutine = run.call_args.args[0]
        coroutine.close()
        self.assertIn('telegram_account', uncached_chat._state.fields_cache)


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
        client.normalize_chat_id.side_effect = lambda value: str(value).lower()
        client.get_chats.return_value = [
            {'id': '79000000001@c.us', 'type': 'user', 'name': 'Client'},
            {'id': 'status@broadcast', 'type': 'user', 'name': 'Status'},
            {'id': 'newsletter@newsletter', 'type': 'user', 'name': 'Channel'},
            {'id': '79000000001-1@g.us', 'type': 'group', 'name': 'Group'},
        ]
        client.get_last_incoming_messages.return_value = [{
            'idMessage': 'm1', 'type': 'incoming', 'timestamp': int(timezone.now().timestamp()),
            'chatId': '79000000001@c.us',
            'typeMessage': 'textMessage', 'textMessage': 'Hello',
        }]
        client.get_last_outgoing_messages.return_value = []

        discovered, imported, _, stats = discover_green(account, None, per_chat=5)

        self.assertEqual((discovered, imported), (1, 1))
        self.assertEqual(stats['available_chats'], 1)
        client.get_chat_history.assert_not_called()
        self.assertFalse(Chat.objects.filter(title='Group').exists())

    @patch('crm_app.services.history_import.GreenAPIClient')
    def test_max_discovery_uses_chat_id_field(self, client_class):
        account = TelegramAccount.objects.create(
            name='MAX', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='5', green_api_token='token',
        )
        client = client_class.return_value
        client.normalize_chat_id.side_effect = lambda value: str(value)
        client.get_chats.return_value = [
            {'chatId': '10000000', 'type': 'user', 'name': 'MAX Client'},
            {'chatId': '-10000000000000', 'type': 'group', 'name': 'MAX Group'},
            {'chatId': '10000002', 'type': 'bot', 'name': 'MAX Bot'},
        ]
        client.get_last_incoming_messages.return_value = [{
            'idMessage': 'max-m1', 'type': 'incoming', 'timestamp': int(timezone.now().timestamp()),
            'chatId': '10000000', 'chatType': 'user',
            'typeMessage': 'textMessage', 'textMessage': 'Hello from MAX',
        }]
        client.get_last_outgoing_messages.return_value = []

        discovered, imported, _, stats = discover_green(account, None, per_chat=5)

        self.assertEqual((discovered, imported), (1, 1))
        self.assertEqual(stats['available_chats'], 1)
        client.get_chat_history.assert_not_called()
        chat = Chat.objects.get(title='MAX Client')
        self.assertEqual(chat.metadata['external_chat_id'], '10000000')
        self.assertFalse(Chat.objects.filter(title__in=['MAX Group', 'MAX Bot']).exists())


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

    @patch('crm_app.services.whatsapp_client.random.uniform', return_value=0)
    @patch('crm_app.services.whatsapp_client.time.sleep')
    def test_rate_limit_is_retried_with_backoff(self, sleep, _uniform):
        account = TelegramAccount(
            account_type=TelegramAccount.AccountType.MAX,
            green_api_instance_id='5', green_api_token='token',
        )
        limited = Mock(ok=False, status_code=429, headers={})
        limited.json.return_value = {'message': 'Too many requests'}
        success = Mock(ok=True, status_code=200, headers={})
        success.json.return_value = [{'idMessage': 'm1'}]
        session = Mock()
        session.request.side_effect = [limited, success]

        result = GreenAPIClient(account, session=session).get_chat_history('1', 5)

        self.assertEqual(result[0]['idMessage'], 'm1')
        self.assertEqual(session.request.call_count, 2)
        self.assertTrue(sleep.called)

    def test_max_get_chats_uses_max_contract_and_history_is_capped(self):
        account = TelegramAccount(
            account_type=TelegramAccount.AccountType.MAX,
            green_api_instance_id='6', green_api_token='token',
        )
        session = Mock()
        response = Mock(ok=True, status_code=200, headers={})
        response.json.side_effect = [[], []]
        session.request.return_value = response
        client = GreenAPIClient(account, session=session)

        client.get_chats(1000)
        client.get_chat_history('10000000', 10000)

        chats_call, history_call = session.request.call_args_list
        self.assertNotIn('params', chats_call.kwargs)
        self.assertEqual(history_call.kwargs['json'], {'chatId': '10000000', 'count': 5000})
