from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from telethon.sessions import StringSession

from crm_app.services.telegram_client_manager import TelegramClientManager
from crm_app.services.whatsapp_client import GreenAPIClient


class MaxGreenAPIClientTests(SimpleTestCase):
    def account(self):
        return SimpleNamespace(
            account_type='max',
            green_api_instance_id='1101000001',
            green_api_token='instance-token',
            green_api_url='https://api.green-api.com',
            green_media_url='https://media.green-api.com',
            green_webhook_token='hook-token',
        )

    def test_max_chat_id_remains_numeric(self):
        client = GreenAPIClient(self.account(), session=Mock())
        self.assertEqual(client.normalize_chat_id('10000000'), '10000000')

    def test_max_text_uses_unified_green_api_endpoint(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {'idMessage': 'max-green-out'}
        session.request.return_value = response

        result = GreenAPIClient(self.account(), session=session).send_text(
            10000000, 'Ответ', quoted_message_id='source-mid'
        )

        self.assertEqual(result, 'max-green-out')
        self.assertIn('/waInstance1101000001/sendMessage/instance-token', session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs['json'], {
            'chatId': '10000000', 'message': 'Ответ', 'quotedMessageId': 'source-mid',
        })


    def test_max_emoji_is_sent_as_unicode_text(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {'idMessage': 'max-emoji'}
        session.request.return_value = response

        GreenAPIClient(self.account(), session=session).send_text(10000000, 'Готово ✅😀')

        self.assertEqual(session.request.call_args.kwargs['json']['message'], 'Готово ✅😀')

    def test_download_file_accepts_direct_string_response(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = 'https://sw-media-3100.storage.yandexcloud.net/file.mp4'
        session.request.return_value = response

        result = GreenAPIClient(self.account(), session=session).get_download_url(
            '10000000', 'message-1'
        )

        self.assertEqual(result, 'https://sw-media-3100.storage.yandexcloud.net/file.mp4')
        self.assertIn('/downloadFile/', session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs['json'], {
            'chatId': '10000000', 'idMessage': 'message-1',
        })

    def test_download_file_accepts_documented_object_response(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {
            'downloadUrl': 'https://sw-media.storage.greenapi.net/file.jpg',
        }
        session.request.return_value = response

        result = GreenAPIClient(self.account(), session=session).get_download_url(
            '10000000', 'message-2'
        )

        self.assertEqual(result, 'https://sw-media.storage.greenapi.net/file.jpg')

    def test_download_file_accepts_nested_url_response(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {
            'downloadUrl': {'url': 'https://storage.greenapi.net/file.jpg'},
        }
        session.request.return_value = response

        result = GreenAPIClient(self.account(), session=session).get_download_url(
            '10000000', 'message-3'
        )

        self.assertEqual(result, 'https://storage.greenapi.net/file.jpg')

    def test_get_message_requests_exact_chat_message(self):
        session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {'idMessage': 'message-3', 'downloadUrl': 'https://storage.greenapi.net/file.jpg'}
        session.request.return_value = response

        result = GreenAPIClient(self.account(), session=session).get_message('10000000', 'message-3')

        self.assertEqual(result['idMessage'], 'message-3')
        self.assertIn('/getMessage/', session.request.call_args.args[1])

    def test_string_error_response_is_reported_without_attribute_error(self):
        session = Mock()
        response = Mock(ok=False, status_code=400, text='bad request')
        response.json.return_value = 'Media is no longer available'
        session.request.return_value = response

        with self.assertRaisesRegex(Exception, 'Media is no longer available'):
            GreenAPIClient(self.account(), session=session).get_download_url(
                '10000000', 'missing-message'
            )


class TelegramProxyTests(SimpleTestCase):
    @override_settings(TELEGRAM_PROXY_URL='socks5://user:password@proxy.local:1080')
    def test_proxy_is_passed_to_telethon(self):
        client = TelegramClientManager()._create_client(
            StringSession(),
            12345,
            '0123456789abcdef0123456789abcdef',
        )

        self.assertIsNotNone(client._proxy)
        self.assertEqual(client._proxy[1], 'proxy.local')
        self.assertEqual(client._proxy[2], 1080)
        self.assertEqual(client._proxy[4], 'user')
        self.assertEqual(client._proxy[5], 'password')
class TelegramEmojiTests(TransactionTestCase):
    def test_telegram_emoji_is_passed_unchanged(self):
        manager = TelegramClientManager()
        manager._clients.clear()
        client = AsyncMock()
        client.send_message.return_value = SimpleNamespace(id=77)
        manager._clients[999] = client
        try:
            result = async_to_sync(manager.send_message)(999, 123, 'Ответ ✅😀')
        finally:
            manager._clients.clear()

        self.assertEqual(result, 77)
        client.send_message.assert_awaited_once_with(123, 'Ответ ✅😀', reply_to=None)

    def test_telegram_reaction_uses_raw_api(self):
        manager = TelegramClientManager()
        manager._clients.clear()
        client = AsyncMock()
        manager._clients[998] = client
        try:
            result = async_to_sync(manager.send_reaction)(998, 123, 77, '🔥')
        finally:
            manager._clients.clear()

        self.assertTrue(result)
        request = client.await_args.args[0]
        self.assertEqual(request.msg_id, 77)
        self.assertEqual(request.reaction[0].emoticon, '🔥')

class TelegramHistoryCatchupTests(SimpleTestCase):
    def test_empty_history_catchup_starts_without_import_error(self):
        client = AsyncMock()
        client.get_dialogs.return_value = []
        account = SimpleNamespace(id=1)

        async_to_sync(TelegramClientManager()._catch_up_history)(client, account)

        client.get_dialogs.assert_awaited_once_with(limit=100)
