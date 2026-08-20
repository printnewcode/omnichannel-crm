import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, ChatAssignment, Message, OutboundDelivery, TelegramAccount
from crm_app.services.outbound_delivery import enqueue_delivery, enqueue_reaction, process_next_delivery


class ProviderWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='provider-operator')

    def test_green_api_text_message_is_ingested_without_assignment(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='10001', green_api_token='api-token',
            green_webhook_token='verify-me',
        )
        url = reverse('whatsapp-webhook', kwargs={'account_id': account.id})
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 10001},
            'timestamp': 1700000000,
            'idMessage': 'green.test',
            'senderData': {
                'chatId': '79990001122@c.us', 'sender': '79990001122@c.us',
                'senderName': 'Ivan',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Hello'},
            },
        }
        response = self.client.post(
            url, data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer verify-me'},
        )
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='green.test')
        self.assertEqual(message.text, 'Hello')
        self.assertEqual(message.chat.telegram_account, account)
        self.assertFalse(ChatAssignment.objects.filter(chat=message.chat).exists())
    def test_max_personal_message_is_ingested_and_token_is_checked(self):
        account = TelegramAccount.objects.create(
            name='MAX personal',
            account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20002',
            green_api_token='max-api-token',
            green_webhook_token='max-hook-token',
        )
        url = reverse('max-webhook', kwargs={'account_id': account.id})
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 20002},
            'timestamp': 1700000000,
            'idMessage': 'max-green-in',
            'senderData': {
                'chatId': '10000000',
                'sender': '10000000',
                'senderName': 'Max User',
                'chatType': 'user',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Question'},
            },
        }
        response = self.client.post(
            url, data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer max-hook-token'},
        )
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='max-green-in')
        self.assertEqual(message.text, 'Question')
        self.assertEqual(message.chat.metadata['external_chat_id'], '10000000')

        forbidden = self.client.post(
            url, data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer wrong'},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_whatsapp_message_sent_from_phone_or_desktop_is_ingested_as_outgoing(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='10011', green_api_token='token', green_webhook_token='hook',
        )
        payload = {
            'typeWebhook': 'outgoingMessageReceived',
            'instanceData': {'idInstance': 10011},
            'timestamp': 1700000000,
            'idMessage': 'wa-phone-out',
            'senderData': {
                'chatId': '79990001133@c.us', 'sender': '79990000000@c.us',
                'chatName': 'Recipient', 'senderName': 'Connected account',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Sent from WhatsApp Desktop'},
            },
        }

        response = self.client.post(
            reverse('whatsapp-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )

        self.assertEqual(response.json()['processed'], 1)
        message = Message.objects.get(external_message_id='wa-phone-out')
        self.assertTrue(message.is_outgoing)
        self.assertEqual(message.status, Message.MessageStatus.SENT)
        self.assertEqual(message.text, 'Sent from WhatsApp Desktop')
        self.assertEqual(message.chat.title, 'Recipient')
        self.assertEqual(message.chat.unread_count, 0)

    def test_max_message_sent_from_phone_or_desktop_is_ingested_as_outgoing(self):
        account = TelegramAccount.objects.create(
            name='MAX', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20011', green_api_token='token', green_webhook_token='hook',
        )
        payload = {
            'typeWebhook': 'outgoingMessageReceived',
            'instanceData': {'idInstance': 20011, 'typeInstance': 'v3'},
            'timestamp': 1700000000,
            'idMessage': 'max-phone-out',
            'senderData': {
                'chatId': '10000011', 'chatType': 'user', 'sender': '10000011',
                'chatName': 'MAX Recipient', 'senderName': 'Connected MAX account',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Sent from MAX'},
            },
        }

        response = self.client.post(
            reverse('max-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )

        self.assertEqual(response.json()['processed'], 1)
        message = Message.objects.get(external_message_id='max-phone-out')
        self.assertTrue(message.is_outgoing)
        self.assertEqual(message.status, Message.MessageStatus.SENT)
        self.assertEqual(message.chat.title, 'MAX Recipient')
        self.assertEqual(message.chat.unread_count, 0)

    @patch('crm_app.tasks.download_green_api_media_task.delay')
    def test_outgoing_whatsapp_media_keeps_quote_and_starts_download(self, download_media):
        account = TelegramAccount.objects.create(
            name='WhatsApp media', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='10013', green_api_token='token', green_webhook_token='hook',
        )
        chat = Chat.objects.create(
            telegram_id=79990001155, telegram_account=account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Recipient', metadata={'external_chat_id': '79990001155@c.us'},
        )
        target = Message.objects.create(
            chat=chat, external_message_id='quoted-in', text='Earlier',
            telegram_date=timezone.now(),
        )
        payload = {
            'typeWebhook': 'outgoingMessageReceived',
            'instanceData': {'idInstance': 10013},
            'timestamp': 1700000000,
            'idMessage': 'wa-photo-out',
            'senderData': {
                'chatId': '79990001155@c.us', 'sender': '79990000000@c.us',
                'chatName': 'Recipient',
            },
            'messageData': {
                'typeMessage': 'imageMessage',
                'fileMessageData': {
                    'downloadUrl': 'https://sw-media.storage.greenapi.net/10013/photo.jpg',
                    'caption': 'Photo from phone', 'mimeType': 'image/jpeg',
                },
                'quotedMessage': {'stanzaId': 'quoted-in'},
            },
        }

        response = self.client.post(
            reverse('whatsapp-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )

        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='wa-photo-out')
        self.assertTrue(message.is_outgoing)
        self.assertEqual(message.message_type, Message.MessageType.PHOTO)
        self.assertEqual(message.reply_to_message, target)
        download_media.assert_called_once_with(message.id)

    def test_outgoing_api_webhook_does_not_duplicate_message_created_by_crm(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp API', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='10012', green_api_token='token', green_webhook_token='hook',
        )
        chat = Chat.objects.create(
            telegram_id=79990001144, telegram_account=account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Recipient', metadata={'external_chat_id': '79990001144@c.us'},
        )
        existing = Message.objects.create(
            chat=chat, external_message_id='wa-api-out', text='Sent from CRM',
            is_outgoing=True, status=Message.MessageStatus.SENT,
            telegram_date=timezone.now(),
        )
        payload = {
            'typeWebhook': 'outgoingAPIMessageReceived',
            'instanceData': {'idInstance': 10012},
            'timestamp': 1700000000,
            'idMessage': 'wa-api-out',
            'senderData': {
                'chatId': '79990001144@c.us', 'sender': '79990000000@c.us',
                'chatName': 'Recipient',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Sent from CRM'},
            },
        }

        response = self.client.post(
            reverse('whatsapp-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.filter(external_message_id='wa-api-out').count(), 1)
        self.assertEqual(Message.objects.get(external_message_id='wa-api-out').id, existing.id)

    def test_provider_bot_peer_is_saved_but_marked_hidden(self):
        account = TelegramAccount.objects.create(
            name='MAX personal', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20004', green_api_token='token',
            green_webhook_token='hook',
        )
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 20004},
            'timestamp': 1700000000,
            'idMessage': 'max-bot-in',
            'senderData': {
                'chatId': '10000001', 'sender': '10000001',
                'senderName': 'MAX bot', 'chatType': 'user', 'isBot': True,
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Bot message'},
            },
        }
        response = self.client.post(
            reverse('max-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='max-bot-in')
        self.assertTrue(message.chat.is_bot)
    def test_max_negative_chat_id_is_treated_as_group_even_if_provider_says_user(self):
        account = TelegramAccount.objects.create(
            name='MAX personal', account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20003', green_api_token='token',
            green_webhook_token='hook',
        )
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 20003},
            'timestamp': 1700000000,
            'idMessage': 'max-group-in',
            'senderData': {
                'chatId': '-70000001', 'sender': '10000000',
                'senderName': 'Group member', 'chatType': 'user',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Group message'},
            },
        }
        response = self.client.post(
            reverse('max-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='max-group-in')
        self.assertEqual(message.chat.chat_type, Chat.ChatType.GROUP)

    def test_green_api_reaction_updates_target_instead_of_creating_message(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='10009', green_api_token='token', green_webhook_token='hook',
        )
        chat = Chat.objects.create(
            telegram_id=79990001122, telegram_account=account,
            chat_type=Chat.ChatType.PRIVATE,
            metadata={'external_chat_id': '79990001122@c.us'},
        )
        target = Message.objects.create(
            chat=chat, external_message_id='original-message', text='Target',
            telegram_date=timezone.now(),
        )
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 10009},
            'idMessage': 'reaction-event',
            'senderData': {
                'chatId': '79990001122@c.us', 'sender': '79990001122@c.us',
            },
            'messageData': {
                'typeMessage': 'reactionMessage',
                'extendedTextMessageData': {'text': '👍'},
                'quotedMessage': {'stanzaId': 'original-message'},
            },
        }

        response = self.client.post(
            reverse('whatsapp-webhook', kwargs={'account_id': account.id}),
            data=json.dumps(payload), content_type='application/json',
            headers={'Authorization': 'Bearer hook'},
        )

        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.metadata['reactions'], [{'emoji': '👍', 'count': 1, 'chosen': False}])
        self.assertFalse(Message.objects.filter(external_message_id='reaction-event').exists())


class OutboundDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='delivery-operator', password='secret')
        self.account = TelegramAccount.objects.create(
            name='Telegram',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
            session_string='test',
        )
        self.chat = Chat.objects.create(
            telegram_id=100,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
        )

    @patch('crm_app.services.outbound_delivery.publish_delivery')
    @patch('crm_app.services.message_router.MessageRouter.send_message', return_value=777)
    def test_outbox_creates_sent_message(self, send_message, publish_delivery):
        delivery = enqueue_delivery(chat=self.chat, text='Reply', requested_by=self.user)
        self.assertTrue(process_next_delivery())
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OutboundDelivery.Status.SENT)
        self.assertEqual(delivery.provider_message_id, '777')
        self.assertEqual(delivery.created_message.text, 'Reply')
        send_message.assert_called_once()
        sent_delivery = publish_delivery.call_args.args[0]
        self.assertEqual(sent_delivery.status, OutboundDelivery.Status.SENT)

    def test_api_enqueues_without_contacting_provider(self):
        self.client.force_login(self.user)
        url = reverse('chat-send-message', kwargs={'pk': self.chat.pk})
        with patch('crm_app.services.message_router.MessageRouter.send_message') as send_message:
            response = self.client.post(
                url,
                data=json.dumps({'text': 'Queued'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'pending')
        self.assertTrue(OutboundDelivery.objects.filter(text='Queued').exists())
        send_message.assert_not_called()

    def test_reaction_api_queues_telegram_reaction(self):
        message = Message.objects.create(
            chat=self.chat, telegram_id=44, text='React to me', telegram_date=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('message-react', kwargs={'pk': message.pk}),
            data=json.dumps({'emoji': '🔥'}), content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        delivery = OutboundDelivery.objects.get(pk=response.json()['delivery_id'])
        self.assertEqual(delivery.reaction_emoji, '🔥')
        self.assertEqual(delivery.reply_to_message, message)

    @patch('crm_app.services.outbound_delivery.publish_message')
    @patch('crm_app.services.outbound_delivery.publish_delivery')
    @patch('crm_app.services.message_router.MessageRouter.send_reaction', return_value=True)
    def test_connector_sends_reaction_without_creating_message(self, send_reaction, publish_delivery, publish_message):
        message = Message.objects.create(
            chat=self.chat, telegram_id=45, text='React to me', telegram_date=timezone.now(),
        )
        delivery = enqueue_reaction(message=message, emoji='👍', requested_by=self.user)

        self.assertTrue(process_next_delivery())

        delivery.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(delivery.status, OutboundDelivery.Status.SENT)
        self.assertIsNone(delivery.created_message)
        self.assertEqual(message.metadata['reactions'], [{'emoji': '👍', 'count': 1, 'chosen': True}])
        send_reaction.assert_called_once_with(message, '👍')
        publish_message.assert_called_once_with(message.id)

    def test_green_api_account_rejects_outgoing_reaction(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )
        chat = Chat.objects.create(
            telegram_id=7999, telegram_account=account, chat_type=Chat.ChatType.PRIVATE,
        )
        message = Message.objects.create(
            chat=chat, external_message_id='wa-message', text='Target', telegram_date=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('message-react', kwargs={'pk': message.pk}),
            data=json.dumps({'emoji': '👍'}), content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(OutboundDelivery.objects.filter(reply_to_message=message).exists())
class TelegramAccountCreationTests(TestCase):
    @patch('crm_app.services.telegram_client_manager.TelegramClientManager.authenticate_account_sync')
    def test_creating_personal_account_does_not_start_mtproto_in_web_process(self, authenticate):
        account = TelegramAccount.objects.create(
            name='Explicit auth only',
            account_type=TelegramAccount.AccountType.PERSONAL,
            phone_number='+79990000000',
            api_id=12345,
            api_hash='0123456789abcdef0123456789abcdef',
        )

        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.INACTIVE)
        authenticate.assert_not_called()

    @patch('crm_app.services.telegram_client_manager.TelegramClientManager.authenticate_account_sync')
    def test_admin_add_personal_account_returns_without_starting_mtproto(self, authenticate):
        admin_user = User.objects.create_superuser(
            username='isolated-admin', password='test-password', email='admin@example.test'
        )
        self.client.force_login(admin_user)

        response = self.client.post(reverse('admin:crm_app_telegramaccount_add'), data={
            'name': 'Saved through Admin',
            'account_type': TelegramAccount.AccountType.PERSONAL,
            'status': TelegramAccount.AccountStatus.INACTIVE,
            'phone_number': '+79990000099',
            'api_id': '12345',
            'api_hash': '0123456789abcdef0123456789abcdef',
            'green_api_url': 'https://api.green-api.com',
            'green_media_url': 'https://media.green-api.com',
            'api_version': 'v23.0',
            'error_count': '0',
            '_save': 'Save',
        })

        self.assertEqual(response.status_code, 302)
        account = TelegramAccount.objects.get(name='Saved through Admin')
        self.assertEqual(account.status, TelegramAccount.AccountStatus.INACTIVE)
        authenticate.assert_not_called()
class TelegramConnectorLifecycleTests(TestCase):
    def test_process_shutdown_keeps_account_active(self):
        from crm_app.services.telegram_client_manager import TelegramClientManager

        account = TelegramAccount.objects.create(
            name='Persistent connector account',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
            session_string='test-session',
        )
        manager = TelegramClientManager()
        manager._clients.clear()
        client = AsyncMock()
        manager._clients[account.id] = client

        async_to_sync(manager.stop_all)()

        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.ACTIVE)
        client.disconnect.assert_awaited_once()
        self.assertNotIn(account.id, manager._clients)

class TelegramAdminConnectorControlTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='connector-admin', password='test-password', email='connector@example.test'
        )
        self.client.force_login(self.admin_user)
        self.account = TelegramAccount.objects.create(
            name='Admin controlled account',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.INACTIVE,
            session_string='test-session',
        )

    @patch('crm_app.services.telegram_client_manager.TelegramClientManager.start_client_sync')
    @patch('crm_app.services.telegram_client_manager.TelegramClientManager.check_authorization_sync')
    def test_start_action_only_records_desired_state(self, check_authorization, start_client):
        response = self.client.post(
            reverse('admin:crm_app_telegramaccount_changelist'),
            data={
                'action': 'start_accounts',
                '_selected_action': [str(self.account.pk)],
                'select_across': '0',
                'index': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, TelegramAccount.AccountStatus.ACTIVE)
        self.assertIsNone(self.account.restart_requested_at)
        start_client.assert_not_called()
        check_authorization.assert_not_called()

    def test_restart_action_sets_connector_request(self):
        response = self.client.post(
            reverse('admin:crm_app_telegramaccount_changelist'),
            data={
                'action': 'restart_accounts',
                '_selected_action': [str(self.account.pk)],
                'select_across': '0',
                'index': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, TelegramAccount.AccountStatus.ACTIVE)
        self.assertIsNotNone(self.account.restart_requested_at)


class TelegramConnectorReconciliationTests(TransactionTestCase):
    def setUp(self):
        from crm_app.services.telegram_client_manager import TelegramClientManager

        self.manager = TelegramClientManager()
        self.manager._clients.clear()
        self.manager._tasks.clear()

    def tearDown(self):
        self.manager._clients.clear()
        self.manager._tasks.clear()

    def test_reconciliation_stops_account_disabled_in_admin(self):
        account = TelegramAccount.objects.create(
            name='Disabled account',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.INACTIVE,
            session_string='test-session',
        )
        client = AsyncMock()
        self.manager._clients[account.id] = client

        async_to_sync(self.manager.start_all_active)()

        client.disconnect.assert_awaited_once()
        self.assertNotIn(account.id, self.manager._clients)
        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.INACTIVE)

    def test_reconciliation_applies_restart_request(self):
        account = TelegramAccount.objects.create(
            name='Restarted account',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
            session_string='test-session',
            restart_requested_at=timezone.now(),
        )
        client = AsyncMock()
        self.manager._clients[account.id] = client

        with patch.object(self.manager, 'start_client', new=AsyncMock(return_value=True)) as start_client:
            async_to_sync(self.manager.start_all_active)()

        client.disconnect.assert_awaited_once()
        start_client.assert_awaited_once()
        account.refresh_from_db()
        self.assertIsNone(account.restart_requested_at)
class AdminNamingTests(TestCase):
    def test_messenger_neutral_names_are_used(self):
        self.assertEqual(TelegramAccount._meta.verbose_name, 'Аккаунт мессенджера')
        self.assertEqual(TelegramAccount._meta.verbose_name_plural, 'Аккаунты мессенджеров')
        self.assertNotIn('Business', TelegramAccount.AccountType.WHATSAPP.label)

class GreenAPIWebhookAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='green-admin', password='test-password', email='green@example.test'
        )
        self.client.force_login(self.admin_user)
        self.account = TelegramAccount.objects.create(
            name='MAX webhook',
            account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20002',
            green_api_token='api-token',
            green_webhook_token='webhook-token',
        )

    @override_settings(DOMAIN='https://crm.example.test')
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.configure_webhook')
    def test_admin_uses_public_domain_instead_of_internal_request_host(self, configure_webhook):
        response = self.client.post(
            reverse('admin:crm_app_telegramaccount_changelist'),
            data={
                'action': 'configure_green_api_webhooks',
                '_selected_action': [str(self.account.pk)],
                'select_across': '0',
                'index': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        configure_webhook.assert_called_once_with(
            f'https://crm.example.test/api/integrations/max/{self.account.pk}/webhook/'
        )

    @override_settings(DOMAIN='http://127.0.0.1:8000')
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.configure_webhook')
    def test_admin_rejects_private_http_webhook(self, configure_webhook):
        response = self.client.post(
            reverse('admin:crm_app_telegramaccount_changelist'),
            data={
                'action': 'configure_green_api_webhooks',
                '_selected_action': [str(self.account.pk)],
                'select_across': '0',
                'index': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        configure_webhook.assert_not_called()
