import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crm_app.models import QuickReply


class QuickReplyApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='quick-reply-admin', password='test', is_staff=True,
        )
        self.client.force_login(self.admin)

    def test_staff_can_create_normalized_reply_and_list_it(self):
        response = self.client.post(
            reverse('quick-reply-list'),
            data=json.dumps({'command': 'INFO', 'text': '  Подробная информация  '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['command'], '/info')
        self.assertEqual(response.json()['text'], 'Подробная информация')
        reply = QuickReply.objects.get()
        self.assertEqual(reply.created_by, self.admin)

        listed = self.client.get(reverse('quick-reply-list'))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]['command'], '/info')

    def test_staff_can_edit_and_delete_reply(self):
        reply = QuickReply.objects.create(command='/hello', text='Привет', created_by=self.admin)

        updated = self.client.patch(
            reverse('quick-reply-detail', args=[reply.id]),
            data=json.dumps({'command': '/hi', 'text': 'Здравствуйте!'}),
            content_type='application/json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['command'], '/hi')

        deleted = self.client.delete(reverse('quick-reply-detail', args=[reply.id]))
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(QuickReply.objects.exists())

    def test_duplicate_and_invalid_commands_are_rejected(self):
        QuickReply.objects.create(command='/info', text='Первый')

        duplicate = self.client.post(
            reverse('quick-reply-list'),
            data={'command': '/INFO', 'text': 'Второй'},
        )
        invalid = self.client.post(
            reverse('quick-reply-list'),
            data={'command': '/привет!', 'text': 'Текст'},
        )

        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('command', duplicate.json())
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('command', invalid.json())

    def test_non_staff_user_cannot_read_or_change_replies(self):
        user = User.objects.create_user(username='operator', password='test')
        self.client.force_login(user)

        response = self.client.get(reverse('quick-reply-list'))

        self.assertEqual(response.status_code, 403)

