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

    def test_workspace_assets_include_smooth_timeline_controls(self):
        with open('frontend/static/frontend/app.js', encoding='utf-8') as source:
            script = source.read()
        with open('frontend/static/frontend/style.css', encoding='utf-8') as source:
            styles = source.read()

        self.assertIn('messageRequestVersion', script)
        self.assertIn('rebuildMessageTimeline', script)
        self.assertIn("divider.className = 'date-divider'", script)
        self.assertIn('conversation-skeleton', styles)
        self.assertIn('.date-divider', styles)

# Create your tests here.
