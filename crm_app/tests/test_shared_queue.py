import json
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm_app.models import Chat, ChatAssignment, Message, OutboundDelivery, TelegramAccount


class SharedConversationQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='shared-queue', password='secret')
        self.client.force_login(self.user)
        self.account = TelegramAccount.objects.create(
            name='Shared Telegram',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )
        self.chat = Chat.objects.create(
            telegram_id=88001,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Общий диалог',
            last_message_at=timezone.now(),
        )
        self.message = Message.objects.create(
            chat=self.chat,
            telegram_id=991,
            text='Исходный вопрос',
            telegram_date=timezone.now(),
        )

    def test_unassigned_chat_is_visible_and_can_send(self):
        self.assertFalse(ChatAssignment.objects.filter(chat=self.chat).exists())
        response = self.client.get(reverse('chat-list'), {'archived': '0'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.chat.id, [item['id'] for item in response.json()['results']])

        response = self.client.post(
            reverse('chat-send-message', kwargs={'pk': self.chat.pk}),
            data=json.dumps({'text': 'Ответ без назначения'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(OutboundDelivery.objects.filter(chat=self.chat, text='Ответ без назначения').exists())

    def test_archive_filters_and_unarchive_restores_chat(self):
        response = self.client.post(reverse('chat-archive', kwargs={'pk': self.chat.pk}))
        self.assertEqual(response.status_code, 200)
        self.chat.refresh_from_db()
        self.assertTrue(self.chat.is_archived)
        archived = self.client.get(reverse('chat-list'), {'archived': '1'}).json()['results']
        active = self.client.get(reverse('chat-list'), {'archived': '0'}).json()['results']
        self.assertIn(self.chat.id, [item['id'] for item in archived])
        self.assertNotIn(self.chat.id, [item['id'] for item in active])

        self.assertEqual(self.client.post(reverse('chat-unarchive', kwargs={'pk': self.chat.pk})).status_code, 200)
        self.chat.refresh_from_db()
        self.assertFalse(self.chat.is_archived)

    def test_reply_endpoint_keeps_source_message(self):
        response = self.client.post(
            reverse('message-reply', kwargs={'pk': self.message.pk}),
            data=json.dumps({'text': 'Цитируемый ответ'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        delivery = OutboundDelivery.objects.get(text='Цитируемый ответ')
        self.assertEqual(delivery.reply_to_message, self.message)

    def test_channel_is_hidden_from_shared_queue(self):
        channel = Chat.objects.create(
            telegram_id=88002,
            telegram_account=self.account,
            chat_type=Chat.ChatType.CHANNEL,
            title='Канал',
        )
        response = self.client.get(reverse('chat-list'))
        self.assertNotIn(channel.id, [item['id'] for item in response.json()['results']])

    def test_frontend_contains_archive_and_reply_controls(self):
        response = self.client.get('/')
        self.assertContains(response, 'id="archive-toggle"')
        self.assertContains(response, 'id="reply-composer"')
        self.assertContains(response, 'id="chat-context-menu"')
    def test_group_chats_and_messages_are_hidden_from_web_api(self):
        group = Chat.objects.create(
            telegram_id=88003,
            telegram_account=self.account,
            chat_type=Chat.ChatType.GROUP,
            title='Hidden group',
        )
        group_message = Message.objects.create(
            chat=group,
            telegram_id=992,
            text='Stored but hidden',
            telegram_date=timezone.now(),
        )

        chats = self.client.get(reverse('chat-list')).json()['results']
        self.assertNotIn(group.id, [item['id'] for item in chats])
        self.assertEqual(
            self.client.get(reverse('message-detail', kwargs={'pk': group_message.pk})).status_code,
            404,
        )

    def test_bot_chats_and_messages_are_hidden_from_web_api(self):
        bot_chat = Chat.objects.create(
            telegram_id=88004,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Hidden bot',
            is_bot=True,
        )
        bot_message = Message.objects.create(
            chat=bot_chat,
            telegram_id=993,
            text='Stored bot message',
            telegram_date=timezone.now(),
        )

        chats = self.client.get(reverse('chat-list')).json()['results']
        self.assertNotIn(bot_chat.id, [item['id'] for item in chats])
        self.assertEqual(
            self.client.get(reverse('message-detail', kwargs={'pk': bot_message.pk})).status_code,
            404,
        )
    def test_frontend_uses_accessible_search_and_full_width_audio(self):
        response = self.client.get('/')
        self.assertContains(response, 'aria-label="Поиск по сообщениям"')
        script = Path('frontend/static/frontend/app.js').read_text(encoding='utf-8')
        styles = Path('frontend/static/frontend/style.css').read_text(encoding='utf-8')
        self.assertIn('class="message__audio"', script)
        self.assertIn('.message__media--audio', styles)
        self.assertIn(':root.light-mode[data-messenger="telegram"]', styles)


class ProviderConversationFilterTests(TestCase):
    def _account(self, account_type, instance):
        return TelegramAccount.objects.create(
            name=account_type,
            account_type=account_type,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id=instance,
            green_api_token='api-token',
            green_webhook_token='hook-token',
        )

    def _post(self, account, provider, sender):
        return self.client.post(
            reverse(f'{provider}-webhook', kwargs={'account_id': account.id}),
            data=json.dumps({
                'typeWebhook': 'incomingMessageReceived',
                'instanceData': {'idInstance': int(account.green_api_instance_id)},
                'timestamp': 1700000000,
                'idMessage': f'{provider}-source-filter',
                'senderData': {**sender, 'sender': sender.get('chatId'), 'senderName': 'Sender'},
                'messageData': {'typeMessage': 'textMessage', 'textMessageData': {'textMessage': 'Hello'}},
            }),
            content_type='application/json',
            headers={'Authorization': 'Bearer hook-token'},
        )

    def test_whatsapp_status_or_channel_source_is_ignored(self):
        account = self._account(TelegramAccount.AccountType.WHATSAPP, '31001')
        response = self._post(account, 'whatsapp', {'chatId': 'status@broadcast'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored')
        self.assertFalse(Message.objects.filter(chat__telegram_account=account).exists())

    def test_provider_counters_increment_once_after_mark_as_read(self):
        from crm_app.services.provider_ingestion import ingest_provider_message

        account = self._account(TelegramAccount.AccountType.MAX, '31003')
        first, _, _ = ingest_provider_message(
            account=account,
            external_chat_id='10001',
            external_message_id='counter-1',
            text='First',
        )
        chat = first.chat
        chat.refresh_from_db()
        self.assertEqual((chat.message_count, chat.unread_count), (1, 1))

        chat.unread_count = 0
        chat.save(update_fields=['unread_count'])
        ingest_provider_message(
            account=account,
            external_chat_id='10001',
            external_message_id='counter-2',
            text='Second',
        )
        chat.refresh_from_db()
        self.assertEqual((chat.message_count, chat.unread_count), (2, 1))
    def test_max_channel_is_ignored_but_group_is_ingested(self):
        account = self._account(TelegramAccount.AccountType.MAX, '31002')
        ignored = self._post(account, 'max', {'chatId': '-70001', 'chatType': 'channel'})
        self.assertEqual(ignored.json()['status'], 'ignored')
        accepted = self._post(account, 'max', {'chatId': '-70002', 'chatType': 'group'})
        self.assertEqual(accepted.json()['status'], 'accepted')
        chat = Chat.objects.get(telegram_account=account)
        self.assertEqual(chat.chat_type, Chat.ChatType.GROUP)