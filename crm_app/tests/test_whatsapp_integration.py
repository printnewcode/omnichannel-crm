import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from crm_app.models import Chat, Message, OutboundDelivery, TelegramAccount
from crm_app.services.outbound_delivery import enqueue_delivery, process_next_delivery
from crm_app.services.provider_media import _allowed_green_api_host, download_green_api_media
from crm_app.services.whatsapp_client import GreenAPIClient, GreenAPIError


class Response:
    def __init__(self, data, status=200, headers=None, chunks=None):
        self._data = data
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.text = json.dumps(data)
        self._chunks = chunks or []

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size=8192):
        return iter(self._chunks)


class GreenAPIClientTests(TestCase):
    def setUp(self):
        self.account = TelegramAccount.objects.create(
            name='WhatsApp', account_type='whatsapp', status='active',
            green_api_instance_id='1101000001', green_api_token='instance-token',
            green_webhook_token='hook-token',
        )

    def test_text_and_quote_payload(self):
        session = Mock()
        session.request.return_value = Response({'idMessage': 'green-out'})
        result = GreenAPIClient(self.account, session=session).send_text(
            '79990000000', 'Hello', quoted_message_id='green-in'
        )
        self.assertEqual(result, 'green-out')
        self.assertIn('/waInstance1101000001/sendMessage/instance-token', session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs['json'], {
            'chatId': '79990000000@c.us', 'message': 'Hello', 'quotedMessageId': 'green-in',
        })

    def test_file_upload_uses_media_host(self):
        with TemporaryDirectory() as media_root:
            Path(media_root, 'answer.pdf').write_bytes(b'pdf')
            session = Mock()
            session.request.return_value = Response({'idMessage': 'green-file'})
            with override_settings(MEDIA_ROOT=Path(media_root)):
                result = GreenAPIClient(self.account, session=session).send_file(
                    '79990000000@c.us', 'answer.pdf', caption='Caption'
                )
        self.assertEqual(result, 'green-file')
        self.assertIn('media.green-api.com/waInstance1101000001/sendFileByUpload/', session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs['data']['fileName'], 'answer.pdf')

    def test_whatsapp_emoji_is_sent_as_unicode_text(self):
        session = Mock()
        session.request.return_value = Response({'idMessage': 'green-emoji'})

        GreenAPIClient(self.account, session=session).send_text('79990000000', 'Готово ✅😀')

        self.assertEqual(session.request.call_args.kwargs['json']['message'], 'Готово ✅😀')
    def test_configure_webhook(self):
        session = Mock()
        session.request.return_value = Response({'saveSettings': True})
        GreenAPIClient(self.account, session=session).configure_webhook('https://crm.example/hook')
        payload = session.request.call_args.kwargs['json']
        self.assertEqual(payload['webhookUrlToken'], 'Bearer hook-token')
        self.assertEqual(payload['incomingWebhook'], 'yes')
        self.assertEqual(payload['outgoingWebhook'], 'yes')

    def test_readable_api_error(self):
        session = Mock()
        session.request.return_value = Response({'message': 'Instance not authorized'}, status=400)
        with self.assertRaisesRegex(GreenAPIError, 'Instance not authorized'):
            GreenAPIClient(self.account, session=session).send_text('1', 'Hello')


class GreenAPIWebhookTests(TestCase):
    def setUp(self):
        self.account = TelegramAccount.objects.create(
            name='WhatsApp', account_type='whatsapp', status='active',
            green_api_instance_id='1101000001', green_api_token='instance-token',
            green_webhook_token='hook-token',
        )
        self.url = reverse('whatsapp-webhook', kwargs={'account_id': self.account.id})

    def post(self, payload, authorization='Bearer hook-token'):
        return self.client.post(
            self.url, json.dumps(payload), content_type='application/json',
            headers={'Authorization': authorization},
        )

    @patch('crm_app.tasks.download_green_api_media_task.delay')
    def test_incoming_media_quote_is_ingested(self, delay):
        chat = Chat.objects.create(
            telegram_id=79990000000, telegram_account=self.account,
            metadata={'external_chat_id': '79990000000@c.us'},
        )
        original = Message.objects.create(
            chat=chat, external_message_id='green-original', text='Earlier',
            telegram_date='2026-01-01T00:00:00Z',
        )
        payload = {
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 1101000001},
            'timestamp': 1700000000,
            'idMessage': 'green-image',
            'senderData': {'chatId': '79990000000@c.us', 'sender': '79990000000@c.us', 'senderName': 'Ivan'},
            'messageData': {
                'typeMessage': 'imageMessage',
                'fileMessageData': {
                    'downloadUrl': 'https://api.green-api.com/files/photo.jpg',
                    'caption': 'Photo', 'fileName': 'photo.jpg', 'mimeType': 'image/jpeg',
                },
                'quotedMessage': {'idMessage': 'green-original'},
            },
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='green-image')
        self.assertEqual(message.message_type, 'photo')
        self.assertEqual(message.reply_to_message, original)
        self.assertEqual(message.metadata['external_chat_id'], '79990000000@c.us')
        delay.assert_called_once_with(message.id)

    def test_authorization_and_instance_are_checked(self):
        payload = {'typeWebhook': 'stateInstanceChanged', 'instanceData': {'idInstance': 999}, 'stateInstance': 'authorized'}
        self.assertEqual(self.post(payload, 'Bearer wrong').status_code, 403)
        response = self.post(payload)
        self.assertEqual(response.json()['status'], 'ignored')

    def test_lid_personal_chat_is_ingested(self):
        response = self.post({
            'typeWebhook': 'incomingMessageReceived',
            'instanceData': {'idInstance': 1101000001},
            'timestamp': 1700000000,
            'idMessage': 'green-lid-message',
            'senderData': {
                'chatId': '123456789012345@lid',
                'sender': '123456789012345@lid',
                'senderName': 'LID Contact',
            },
            'messageData': {
                'typeMessage': 'textMessage',
                'textMessageData': {'textMessage': 'Hello from LID'},
            },
        })

        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(external_message_id='green-lid-message')
        self.assertEqual(message.chat.chat_type, Chat.ChatType.PRIVATE)
        self.assertEqual(message.chat.metadata['external_chat_id'], '123456789012345@lid')

    def test_outgoing_read_status_updates_message(self):
        chat = Chat.objects.create(telegram_id=7999, telegram_account=self.account)
        message = Message.objects.create(
            chat=chat, external_message_id='green-out', text='Hi', is_outgoing=True,
            telegram_date='2026-01-01T00:00:00Z', status='sent',
        )
        response = self.post({
            'typeWebhook': 'outgoingMessageStatus',
            'instanceData': {'idInstance': 1101000001},
            'idMessage': 'green-out', 'status': 'read',
        })
        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertEqual(message.metadata['provider_status'], 'read')


class GreenAPIMediaDownloadTests(TestCase):
    def test_streaming_download_updates_message(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type='whatsapp', status='active',
            green_api_instance_id='1', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=7999, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='m1', message_type='photo', media_file_id='m1',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={'download_url': 'https://do-media-7107.fra1.digitaloceanspaces.com/files/x.jpg', 'provider_content': {'fileName': 'x.jpg'}},
        )
        response = Response({}, headers={'Content-Type': 'image/jpeg'}, chunks=[b'ab', b'cd'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'abcd')
        message.refresh_from_db()
        self.assertEqual(message.metadata['media_size'], 4)

    def test_legacy_string_provider_content_does_not_break_download(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp legacy', account_type='whatsapp', status='active',
            green_api_instance_id='1', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=7996, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='legacy-content', message_type='photo',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={
                'download_url': 'https://sw-media.storage.greenapi.net/1/legacy.jpg',
                'provider_content': 'legacy serialized value',
            },
        )
        response = Response({}, headers={'Content-Type': 'image/jpeg'}, chunks=[b'legacy-photo'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'legacy-photo')

    @patch(
        'crm_app.services.whatsapp_client.GreenAPIClient.get_download_url',
        return_value='https://sw-media.storage.greenapi.net/1/legacy-root.jpg',
    )
    def test_legacy_string_metadata_is_normalized(self, refresh_url):
        account = TelegramAccount.objects.create(
            name='WhatsApp legacy root', account_type='whatsapp', status='active',
            green_api_instance_id='1', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=7995, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='legacy-root', message_type='photo',
            telegram_date='2026-01-01T00:00:00Z', metadata='legacy metadata',
        )
        response = Response({}, headers={'Content-Type': 'image/jpeg'}, chunks=[b'legacy-root-photo'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'legacy-root-photo')
        message.refresh_from_db()
        self.assertIsInstance(message.metadata, dict)
        self.assertEqual(message.metadata['download_url'], refresh_url.return_value)

    def test_max_yandex_cluster_media_is_allowed(self):
        account = TelegramAccount.objects.create(
            name='MAX media', account_type='max', status='active',
            green_api_instance_id='3100000000', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=10000000, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='max-media-1', message_type='video',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={
                'download_url': 'https://sw-media.storage.yandexcloud.net/310022706347/video.mp4',
                'provider_content': {'fileName': 'video.mp4'},
            },
        )
        response = Response({}, headers={'Content-Type': 'video/mp4'}, chunks=[b'max-video'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'max-video')

    def test_all_documented_max_storage_hosts_are_allowed(self):
        account = TelegramAccount(
            name='MAX media hosts', account_type='max',
            green_api_url='https://api.green-api.com',
            green_media_url='https://media.green-api.com',
        )
        for hostname in (
            'sw-media.storage.yandexcloud.net',
            'sw-media-3100.storage.yandexcloud.net',
            'sw-media-out.storage.yandexcloud.net',
            'media-3100.storage.yandexcloud.net',
        ):
            with self.subTest(hostname=hostname):
                self.assertTrue(_allowed_green_api_host(hostname, account))

    def test_whatsapp_official_greenapi_storage_is_allowed(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp storage', account_type='whatsapp', status='active',
            green_api_instance_id='1101000000', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=7998, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='wa-media-1', message_type='photo',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={
                'download_url': 'https://sw-media.storage.greenapi.net/1101000000/photo.jpg',
                'provider_content': {'fileName': 'photo.jpg'},
            },
        )
        response = Response({}, headers={'Content-Type': 'image/jpeg'}, chunks=[b'whatsapp-photo'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'whatsapp-photo')

    def test_whatsapp_greenapi_storage_cluster_subdomains_are_allowed(self):
        account = TelegramAccount(
            name='WhatsApp CDN hosts', account_type='whatsapp',
            green_api_url='https://api.green-api.com',
            green_media_url='https://media.green-api.com',
        )
        for hostname in (
            'sw-media.storage.greenapi.net',
            'sw-media-1101.storage.greenapi.net',
            'media.storage.greenapi.net',
        ):
            with self.subTest(hostname=hostname):
                self.assertTrue(_allowed_green_api_host(hostname, account))

    @patch(
        'crm_app.services.whatsapp_client.GreenAPIClient.get_download_url',
        return_value='https://sw-media.storage.greenapi.net/1101000000/refreshed.jpg',
    )
    def test_missing_whatsapp_history_url_is_refreshed_via_download_file(self, refresh_url):
        account = TelegramAccount.objects.create(
            name='WhatsApp history', account_type='whatsapp', status='active',
            green_api_instance_id='1101000000', green_api_token='token',
        )
        chat = Chat.objects.create(
            telegram_id=7997, telegram_account=account,
            metadata={'external_chat_id': '7997@c.us'},
        )
        message = Message.objects.create(
            chat=chat, external_message_id='wa-history-media', message_type='photo',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={'download_url': '', 'provider_content': {'fileName': 'history.jpg'}},
        )
        response = Response({}, headers={'Content-Type': 'image/jpeg'}, chunks=[b'history-photo'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=Path(media_root)), patch(
            'crm_app.services.provider_media.requests.get', return_value=response
        ):
            relative = download_green_api_media(message)
            self.assertEqual((Path(media_root) / relative).read_bytes(), b'history-photo')
        refresh_url.assert_called_once_with('7997@c.us', 'wa-history-media')
        message.refresh_from_db()
        self.assertEqual(message.metadata['download_url'], refresh_url.return_value)

    @patch('crm_app.services.whatsapp_client.GreenAPIClient.get_download_url', return_value=None)
    def test_green_api_media_rejects_unrelated_object_storage_host(self, refresh_url):
        account = TelegramAccount.objects.create(
            name='Unsafe media', account_type='whatsapp', status='active',
            green_api_instance_id='7107000000', green_api_token='token',
        )
        chat = Chat.objects.create(telegram_id=7888, telegram_account=account)
        message = Message.objects.create(
            chat=chat, external_message_id='unsafe-m1', message_type='photo',
            telegram_date='2026-01-01T00:00:00Z',
            metadata={'download_url': 'https://attacker.digitaloceanspaces.com/x.jpg'},
        )

        with self.assertRaisesRegex(ValueError, 'Unsafe GREEN-API media URL'):
            download_green_api_media(message)
        refresh_url.assert_called_once()


class GreenAPIOutboundAndFrontendTests(TestCase):
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.send', return_value='green-sent')
    def test_outbox_sends_document(self, send):
        account = TelegramAccount.objects.create(
            name='WhatsApp', account_type='whatsapp', status='active',
            green_api_instance_id='1', green_api_token='token',
        )
        chat = Chat.objects.create(
            telegram_id=7999, telegram_account=account,
            metadata={'external_chat_id': '120363000000000000@g.us'},
        )
        delivery = enqueue_delivery(chat=chat, text='Document', media_path='uploads/report.pdf')
        self.assertTrue(process_next_delivery())
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OutboundDelivery.Status.SENT)
        self.assertEqual(delivery.created_message.message_type, 'document')
        send.assert_called_once_with('120363000000000000@g.us', text='Document', media_path='uploads/report.pdf', reply_to_id=None)

    def test_authenticated_home_contains_whatsapp_controls(self):
        user = User.objects.create_user(username='wa-ui')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'data-messenger="whatsapp"')
        self.assertContains(response, 'id="media-upload"')
        self.assertContains(response, 'id="emoji-btn"')
        self.assertContains(response, 'id="emoji-picker"')
        self.assertContains(response, 'multiple')
class MaxPersonalGreenAPIIntegrationTests(TestCase):
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.send', return_value='max-green-sent')
    def test_outbox_sends_max_document_to_numeric_chat(self, send):
        account = TelegramAccount.objects.create(
            name='MAX personal', account_type='max', status='active',
            green_api_instance_id='2', green_api_token='max-token',
        )
        chat = Chat.objects.create(
            telegram_id=10000000, telegram_account=account,
            metadata={'external_chat_id': '10000000'},
        )
        source = Message.objects.create(
            chat=chat, external_message_id='max-source', text='Question',
            telegram_date='2026-01-01T00:00:00Z',
        )
        delivery = enqueue_delivery(
            chat=chat, text='Answer', media_path='uploads/answer.pdf',
            reply_to_message=source,
        )

        self.assertTrue(process_next_delivery())
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OutboundDelivery.Status.SENT)
        self.assertEqual(delivery.provider_message_id, 'max-green-sent')
        send.assert_called_once_with(
            '10000000', text='Answer', media_path='uploads/answer.pdf',
            reply_to_id='max-source',
        )

    def test_authenticated_home_contains_personal_max_controls(self):
        user = User.objects.create_user(username='max-ui')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'data-messenger="max"')
        self.assertContains(response, 'id="media-upload"')
        self.assertContains(response, 'id="emoji-btn"')
        self.assertContains(response, 'id="emoji-picker"')
        self.assertContains(response, 'multiple')
