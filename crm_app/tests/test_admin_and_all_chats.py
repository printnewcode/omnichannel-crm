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

    def test_service_fields_are_read_only_status_is_editable_and_account_type_locks_after_creation(self):
        add_fields = set(self.model_admin.get_readonly_fields(self.request))
        self.assertTrue({
            'session_string', 'telegram_user_id', 'first_name', 'last_name',
            'username', 'last_error', 'error_count', 'last_activity',
        }.issubset(add_fields))
        self.assertNotIn('status', add_fields)

        account = TelegramAccount(name='Support', account_type=TelegramAccount.AccountType.PERSONAL)
        change_fields = set(self.model_admin.get_readonly_fields(self.request, account))
        self.assertIn('account_type', change_fields)
        self.assertNotIn('status', change_fields)


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

    def test_chat_api_paginates_large_visible_list_without_a_fifty_chat_cap(self):
        account = self.accounts[TelegramAccount.AccountType.PERSONAL]
        Chat.objects.bulk_create([
            Chat(
                telegram_id=93000 + index,
                telegram_account=account,
                chat_type=Chat.ChatType.PRIVATE,
                title=f'Client {index}',
                last_message_at=timezone.now() - timedelta(seconds=index),
            )
            for index in range(202)
        ])

        first = self.client.get(reverse('chat-list'), {'page_size': 100}).json()
        second = self.client.get(first['next']).json()
        third = self.client.get(second['next']).json()

        self.assertEqual(first['count'], 205)
        self.assertEqual(len(first['results']), 100)
        self.assertEqual(len(second['results']), 100)
        self.assertEqual(len(third['results']), 5)

    def test_chat_api_filters_messenger_archive_and_search_on_server(self):
        self.chats[0].is_archived = True
        self.chats[0].save(update_fields=['is_archived'])

        max_archive = self.client.get(reverse('chat-list'), {
            'messenger': 'max',
            'archived': '1',
            'search': 'max',
        }).json()
        whatsapp_active = self.client.get(reverse('chat-list'), {
            'messenger': 'whatsapp',
            'archived': '0',
        }).json()

        self.assertEqual([item['id'] for item in max_archive['results']], [self.chats[0].id])
        self.assertEqual(max_archive['active_count'], 0)
        self.assertEqual(max_archive['archive_count'], 1)
        self.assertEqual([item['id'] for item in whatsapp_active['results']], [self.chats[1].id])

    def test_chat_list_uses_compact_account_payload(self):
        result = self.client.get(reverse('chat-list')).json()['results'][0]

        self.assertEqual(
            set(result['telegram_account']),
            {'id', 'name', 'account_type', 'status'},
        )
        self.assertNotIn('metadata', result)

    def test_chat_api_uses_cached_latest_message_order(self):
        latest = Message.objects.create(
            chat=self.chats[2],
            telegram_id=93001,
            text='Фактически самое новое сообщение',
            telegram_date=timezone.now() + timedelta(minutes=1),
        )
        self.chats[2].last_message_at = latest.telegram_date
        self.chats[2].save(update_fields=['last_message_at'])

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
        self.assertIn("page_size: '50'", script)
        self.assertIn('fetchChats({loadMore: true})', script)
        self.assertNotIn('while (nextUrl', script)
