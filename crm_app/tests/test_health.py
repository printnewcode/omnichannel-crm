from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient


class HealthCheckTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_check_confirms_database(self):
        response = self.client.get('/api/health/', secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'healthy')
        self.assertEqual(response.json()['database'], 'healthy')

    @patch('crm_app.views.connection.cursor', side_effect=RuntimeError('database unavailable'))
    def test_health_check_returns_503_when_database_fails(self, _cursor):
        response = self.client.get('/api/health/', secure=True)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unhealthy')
        self.assertEqual(response.json()['database'], 'unavailable')
