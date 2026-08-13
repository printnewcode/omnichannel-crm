from django.contrib.auth.models import User
from django.test import TestCase


class HistoryImportUiTests(TestCase):
    def test_authenticated_workspace_contains_import_controls(self):
        user = User.objects.create_user('ui-user', password='secret')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'id="import-chats-btn"')
        self.assertContains(response, 'id="import-history-btn"')
        self.assertContains(response, 'id="import-history-modal"')
        self.assertContains(response, 'id="import-chats-modal"')

# Create your tests here.
