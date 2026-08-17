import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from crm_app.admin import TelegramAccountAdmin, TelegramAccountAdminForm
from crm_app.models import Chat, HistoryImportJob, Message, TelegramAccount


class MessengerAccountAdminTests(TestCase):
    def setUp(self):
        self.model_admin = TelegramAccountAdmin(TelegramAccount, admin.site)
        self.request = RequestFactory().get('/admin/crm_app/telegramaccount/add/')

    def test_external_credentials_have_clear_help_links(self):
        form = TelegramAccountAdminForm()

        self.assertIn('my.telegram.org/apps', str(form.fields['api_id'].help_text))
        self.assertIn('t.me/BotFather', str(form.fields['bot_token'].help_text))
        self.assertIn('console.green-api.com', str(form.fields['green_api_instance_id'].help_text))
        self.assertIn('без символа @', str(form.fields['bot_username'].help_text))

    def test_service_fields_are_read_only_and_account_type_locks_after_creation(self):
        add_fields = set(self.model_admin.get_readonly_fields(self.request))
        self.assertTrue({
            'status', 'session_string', 'telegram_user_id', 'first_name', 'last_name',
            'username', 'last_error', 'error_count', 'last_activity',
        }.issubset(add_fields))

        account = TelegramAccount(name='Support', account_type=TelegramAccount.AccountType.PERSONAL)
        change_fields = set(self.model_admin.get_readonly_fields(self.request, account))
        self.assertIn('account_type', change_fields)


class AllMessengerConversationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='all-messengers', password='secret')
        self.client.force_login(self.user)
        now = timezone.now()
        self.accounts = {
            account_type: TelegramAccount.objects.create(
                name=account_type,
                account_type=account_type,
                status=TelegramAccount.AccountStatus.ACTIVE,
            )
            for account_type in (
                TelegramAccount.AccountType.PERSONAL,
                TelegramAccount.AccountType.MAX,
                TelegramAccount.AccountType.WHATSAPP,
            )
        }
        self.chats = [
            Chat.objects.create(
                telegram_id=92000 + index,
                telegram_account=self.accounts[account_type],
                chat_type=Chat.ChatType.PRIVATE,
                title=account_type,
                last_message_at=now - timedelta(minutes=index),
            )
            for index, account_type in enumerate((
                TelegramAccount.AccountType.MAX,
                TelegramAccount.AccountType.WHATSAPP,
                TelegramAccount.AccountType.PERSONAL,
            ))
        ]

    def test_chat_api_returns_all_messengers_in_last_message_order(self):
        response = self.client.get(reverse('chat-list'))

        self.assertEqual(response.status_code, 200)
        results = response.json()['results']
        self.assertEqual([item['id'] for item in results[:3]], [chat.id for chat in self.chats])
        self.assertEqual(
            [item['telegram_account']['account_type'] for item in results[:3]],
            ['max', 'whatsapp', 'personal'],
        )

    def test_chat_api_uses_real_latest_message_when_cached_date_is_stale(self):
        Message.objects.create(
            chat=self.chats[2],
            telegram_id=93001,
            text='Фактически самое новое сообщение',
            telegram_date=timezone.now() + timedelta(minutes=1),
        )

        results = self.client.get(reverse('chat-list')).json()['results']

        self.assertEqual(results[0]['id'], self.chats[2].id)

    @patch('crm_app.tasks.run_history_import.delay')
    def test_all_import_queues_each_personal_provider_account(self, delay):
        response = self.client.post(
            reverse('telegram-account-import-chats'),
            data=json.dumps({'messenger': 'all'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(HistoryImportJob.objects.count(), 3)
        self.assertEqual(delay.call_count, 3)

    def test_frontend_has_all_messenger_view_and_source_badges(self):
        response = self.client.get('/')
        script = Path('frontend/static/frontend/app.js').read_text(encoding='utf-8')

        self.assertContains(response, 'data-messenger="all"')
        self.assertIn('messenger-badge--${messenger}', script)
        self.assertIn('messenger === "all" || getMessenger(chat) === messenger', script)
