import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from crm_app.models import TelegramAccount


class AccountHealthApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='health-admin', password='test', is_staff=True,
        )
        self.client.force_login(self.admin)

    def test_health_lists_accounts_without_requiring_chats(self):
        account = TelegramAccount.objects.create(
            name='Offline MAX',
            account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ERROR,
            last_error='instance offline',
        )

        response = self.client.get(reverse('telegram-account-health'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['accounts'][0]
        self.assertEqual(payload['id'], account.id)
        self.assertEqual(payload['last_error'], 'instance offline')
        self.assertTrue(payload['can_start'])
        self.assertIn(f'/{account.id}/change/', payload['admin_url'])

    def test_personal_start_is_delegated_to_connector(self):
        account = TelegramAccount.objects.create(
            name='Personal Telegram',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.INACTIVE,
            api_id=12345,
            api_hash='hash',
            session_string='session',
        )

        response = self.client.post(reverse('telegram-account-start', args=[account.id]))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'starting')
        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.ACTIVE)
        self.assertIsNotNone(account.restart_requested_at)

    def test_personal_start_failure_points_to_admin(self):
        account = TelegramAccount.objects.create(
            name='Telegram without session',
            account_type=TelegramAccount.AccountType.PERSONAL,
            api_id=12345,
            api_hash='hash',
        )

        response = self.client.post(reverse('telegram-account-start', args=[account.id]))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()['requires_admin'])
        self.assertIn('админке', response.json()['error'])
        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.ERROR)

    @override_settings(DOMAIN='https://crm.example.test')
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.configure_webhook')
    @patch('crm_app.services.whatsapp_client.GreenAPIClient.get_state_instance')
    def test_green_account_start_checks_authorization_and_configures_webhook(
        self, get_state, configure_webhook,
    ):
        get_state.return_value = {'stateInstance': 'authorized'}
        account = TelegramAccount.objects.create(
            name='WhatsApp',
            account_type=TelegramAccount.AccountType.WHATSAPP,
            status=TelegramAccount.AccountStatus.INACTIVE,
            green_api_instance_id='10001',
            green_api_token='token',
        )

        response = self.client.post(reverse('telegram-account-start', args=[account.id]))

        self.assertEqual(response.status_code, 200)
        configure_webhook.assert_called_once_with(
            f'https://crm.example.test/api/integrations/whatsapp/{account.id}/webhook/'
        )
        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.ACTIVE)
        self.assertIsNotNone(account.last_activity)

    def test_non_staff_user_cannot_start_account(self):
        user = User.objects.create_user(username='operator', password='test')
        self.client.force_login(user)
        account = TelegramAccount.objects.create(
            name='Offline', account_type=TelegramAccount.AccountType.BOT,
        )

        response = self.client.post(reverse('telegram-account-start', args=[account.id]))

        self.assertEqual(response.status_code, 403)


class GreenAccountStateWebhookTests(TestCase):
    def test_state_webhook_updates_account_status(self):
        account = TelegramAccount.objects.create(
            name='MAX',
            account_type=TelegramAccount.AccountType.MAX,
            status=TelegramAccount.AccountStatus.ACTIVE,
            green_api_instance_id='20001',
            green_api_token='token',
            green_webhook_token='verify-me',
        )
        url = reverse('max-webhook', kwargs={'account_id': account.id})

        response = self.client.post(
            url,
            data=json.dumps({
                'typeWebhook': 'stateInstanceChanged',
                'instanceData': {'idInstance': 20001},
                'stateInstance': 'notAuthorized',
            }),
            content_type='application/json',
            headers={'Authorization': 'Bearer verify-me'},
        )

        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.status, TelegramAccount.AccountStatus.ERROR)
        self.assertIn('notAuthorized', account.last_error)
