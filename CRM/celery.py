"""
Celery configuration для CRM проекта
"""
import os
from celery import Celery

# Установка Django settings модуля
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')

app = Celery('CRM')

# Загрузка конфигурации из settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматическое обнаружение задач из всех установленных приложений
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Задача для отладки"""
    print(f'Request: {self.request!r}')
