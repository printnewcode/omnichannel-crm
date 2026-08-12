import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, Message, OutboundDelivery, TelegramAccount
from crm_app.services.telegram_client_manager import TelegramClientManager


class AttachmentApiTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=Path(self.temp_media.name), MEDIA_URL='/media/')
        self.override.enable()
        self.user = User.objects.create_user(username='attachment-user', password='secret')
        self.client.force_login(self.user)
        self.account = TelegramAccount.objects.create(
            name='Personal',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
            session_string='session',
        )
        self.chat = Chat.objects.create(
            telegram_id=99901,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Attachment chat',
        )

    def tearDown(self):
        self.override.disable()
        self.temp_media.cleanup()

    def test_upload_accepts_svg_and_preserves_original_filename(self):
        upload = SimpleUploadedFile(
            'original-vector.svg',
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type='image/svg+xml',
        )
        response = self.client.post(reverse('file-upload'), {'file': upload})

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload['file_name'], 'original-vector.svg')
        self.assertTrue(payload['file_path'].endswith('/original-vector.svg'))
        self.assertTrue((Path(self.temp_media.name) / payload['file_path']).is_file())

    def test_upload_reports_empty_file_separately(self):
        upload = SimpleUploadedFile('empty.bin', b'', content_type='application/octet-stream')
        response = self.client.post(reverse('file-upload'), {'file': upload})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'file_empty')

    def test_one_request_enqueues_multiple_attachments(self):
        response = self.client.post(
            reverse('chat-send-message', kwargs={'pk': self.chat.pk}),
            data={
                'text': 'Caption',
                'media_paths': ['uploads/a/report.svg', 'uploads/b/archive.unknown'],
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(len(payload['delivery_ids']), 2)
        deliveries = list(OutboundDelivery.objects.filter(chat=self.chat).order_by('id'))
        self.assertEqual([item.media_path for item in deliveries], [
            'uploads/a/report.svg', 'uploads/b/archive.unknown'
        ])
        self.assertEqual([item.text for item in deliveries], ['Caption', ''])

    def test_message_search_filters_text_and_media_captions(self):
        Message.objects.create(
            chat=self.chat, telegram_id=201, text='Уникальный вопрос клиента',
            message_type=Message.MessageType.TEXT, telegram_date=timezone.now(),
        )
        Message.objects.create(
            chat=self.chat, telegram_id=202, text='Совсем другой текст',
            media_caption='Нужная подпись к файлу',
            message_type=Message.MessageType.DOCUMENT, telegram_date=timezone.now(),
        )

        text_response = self.client.get(
            reverse('message-by-chat'), {'chat_id': self.chat.pk, 'search': 'уникальный'}
        )
        caption_response = self.client.get(
            reverse('message-by-chat'), {'chat_id': self.chat.pk, 'search': 'подпись'}
        )

        self.assertEqual(text_response.status_code, 200)
        self.assertEqual([item['telegram_id'] for item in text_response.json()['results']], [201])
        self.assertEqual([item['telegram_id'] for item in caption_response.json()['results']], [202])
    def test_download_error_is_specific(self):
        message = Message.objects.create(
            chat=self.chat,
            telegram_id=123,
            message_type=Message.MessageType.PHOTO,
            status=Message.MessageStatus.RECEIVED,
            telegram_date=timezone.now(),
        )
        with patch.object(
            TelegramClientManager,
            'download_media_by_message_id_sync',
            side_effect=RuntimeError('Telegram media expired'),
        ):
            response = self.client.get(
                reverse('message-download-media', kwargs={'pk': message.pk})
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['code'], 'provider_download_failed')
        self.assertIn('Telegram media expired', response.json()['error'])


class TelegramIncomingMediaTests(TransactionTestCase):
    def test_connected_client_download_preserves_filename(self):
        with tempfile.TemporaryDirectory() as temp_media:
            account = TelegramAccount.objects.create(
                name='Media account',
                account_type=TelegramAccount.AccountType.PERSONAL,
                status=TelegramAccount.AccountStatus.ACTIVE,
            )
            chat = Chat.objects.create(
                telegram_id=99902,
                telegram_account=account,
                chat_type=Chat.ChatType.PRIVATE,
            )
            record = Message.objects.create(
                chat=chat,
                telegram_id=321,
                message_type=Message.MessageType.DOCUMENT,
                status=Message.MessageStatus.RECEIVED,
                telegram_date=timezone.now(),
            )
            telegram_message = SimpleNamespace(
                id=321,
                file=SimpleNamespace(name='source-name.svg'),
                document=SimpleNamespace(),
            )

            class FakeClient:
                async def download_media(self, _message, file):
                    Path(file).write_bytes(b'<svg></svg>')
                    return file

            with override_settings(MEDIA_ROOT=Path(temp_media)):
                path = async_to_sync(TelegramClientManager()._download_media_telethon)(
                    FakeClient(), telegram_message, record
                )

            record.refresh_from_db()
            self.assertTrue(path.endswith('/source-name.svg'))
            self.assertEqual(record.metadata['original_filename'], 'source-name.svg')
            self.assertTrue((Path(temp_media) / path).is_file())