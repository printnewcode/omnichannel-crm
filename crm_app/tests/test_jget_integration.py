import json
import time
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, ChatAssignment, Message, TelegramAccount
from crm_app.services.jget_bridge import build_signature
from crm_app.services.message_router import MessageRouter


class JgetQuestionWebhookTests(TestCase):
    def setUp(self):
        self.account = TelegramAccount.objects.create(
            name='JGET',
            account_type=TelegramAccount.AccountType.BOT,
            status=TelegramAccount.AccountStatus.ACTIVE,
            bot_token='123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHI',
            bridge_secret='test-secret',
            bridge_url='https://bot.example/api/crm/questions/{question_id}/reply/',
        )
        self.url = reverse('jget-question-webhook', kwargs={'account_id': self.account.id})
        self.payload = {
            'event_id': 'jget-question-15',
            'question_id': 15,
            'telegram_message_id': 44,
            'telegram_chat_id': 100500,
            'telegram_user_id': 100500,
            'text': 'Нужна помощь',
            'created_at': timezone.now().isoformat(),
            'user': {
                'name': 'Иван Иванов',
                'username': 'ivan',
                'role': 'parent',
                'city_id': 608,
            },
        }

    def _post(self, payload=None, secret='test-secret'):
        body = json.dumps(payload or self.payload, ensure_ascii=False).encode('utf-8')
        timestamp = str(int(time.time()))
        return self.client.post(
            self.url,
            data=body,
            content_type='application/json',
            headers={
                'X-CRM-Timestamp': timestamp,
                'X-CRM-Signature': build_signature(secret, timestamp, body),
            },
        )

    def test_creates_message_and_shared_chat(self):
        response = self._post()

        self.assertEqual(response.status_code, 201)
        message = Message.objects.get()
        self.assertEqual(message.text, 'Нужна помощь')
        self.assertEqual(message.metadata['question_id'], 15)
        self.assertEqual(message.chat.telegram_account, self.account)
        self.assertEqual(message.chat.metadata['jget']['question_id'], 15)
        self.assertFalse(ChatAssignment.objects.filter(chat=message.chat).exists())

    def test_duplicate_delivery_is_idempotent(self):
        self.assertEqual(self._post().status_code, 201)
        self.assertEqual(self._post().status_code, 200)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Chat.objects.count(), 1)

    def test_rejects_invalid_signature(self):
        response = self._post(secret='wrong-secret')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Message.objects.exists())


class JgetReplyRoutingTests(TestCase):
    def test_routes_bot_reply_through_jget_bridge(self):
        account = TelegramAccount.objects.create(
            name='JGET',
            account_type=TelegramAccount.AccountType.BOT,
            status=TelegramAccount.AccountStatus.ACTIVE,
            bot_token='987654321:abcdefghijklmnopqrstuvwxyzABCDEFGHI',
            bridge_secret='test-secret',
            bridge_url='https://bot.example/api/crm/questions/{question_id}/reply/',
        )
        chat = Chat.objects.create(
            telegram_id=42,
            telegram_account=account,
            chat_type=Chat.ChatType.PRIVATE,
            metadata={'jget': {'question_id': 99}},
        )
        incoming = Message.objects.create(
            telegram_id=7,
            chat=chat,
            text='Вопрос',
            telegram_date=timezone.now(),
            metadata={'question_id': 99},
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'telegram_message_id': 1234}

        with patch('crm_app.services.jget_bridge.requests.post', return_value=response) as post:
            result = MessageRouter().send_reply(incoming, 'Ответ')

        self.assertEqual(result, 1234)
        self.assertIn('/questions/99/reply/', post.call_args.args[0])
