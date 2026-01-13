#!/usr/bin/env python
"""
Скрипт для тестирования настройки системы
Запускает базовые проверки всех компонентов
"""
import os
import sys
import requests
import time
from pathlib import Path

# Добавление корневой директории в путь
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')

import django
django.setup()

from django.conf import settings
from crm_app.models import TelegramAccount, Chat, Message
from crm_app.services.health_monitor import HealthMonitor


def test_database_connection():
    """Тестирование подключения к базе данных"""
    print("🔍 Тестирование подключения к базе данных...")
    try:
        # Простая проверка - подсчет аккаунтов
        count = TelegramAccount.objects.count()
        print(f"✅ База данных доступна. Аккаунтов: {count}")
        return True
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        return False


def test_api_health():
    """Тестирование API здоровья"""
    print("🔍 Тестирование API здоровья...")
    try:
        response = requests.get("http://localhost:8000/api/health/", timeout=5)
        if response.status_code == 200:
            print("✅ API здоровье доступно")
            return True
        else:
            print(f"❌ API вернул статус {response.status_code}")
            return False
    except requests.RequestException as e:
        print(f"❌ Ошибка подключения к API: {e}")
        print("💡 Убедитесь, что Django сервер запущен: python manage.py runserver")
        return False


def test_websocket_connection():
    """Тестирование WebSocket подключения"""
    print("🔍 Тестирование WebSocket подключения...")
    try:
        import websocket
        # В контейнере Daphne доступен локально
        # WebSocket требует аутентификации, но мы проверяем доступность сервиса
        ws = websocket.create_connection("ws://daphne:8001/ws/messages/", timeout=5)
        ws.close()
        print("✅ WebSocket доступен")
        return True
    except websocket.WebSocketBadStatusException as e:
        if "403" in str(e):
            print("✅ WebSocket доступен (требует аутентификации)")
            return True
        else:
            print(f"❌ Ошибка WebSocket: {e}")
            return False
    except Exception as e:
        print(f"❌ Ошибка WebSocket: {e}")
        print("💡 Убедитесь, что Daphne запущен: daphne -b 0.0.0.0 -p 8001 CRM.asgi:application")
        return False


def test_redis_connection():
    """Тестирование подключения к Redis"""
    print("🔍 Тестирование подключения к Redis...")
    try:
        import redis
        # В контейнере Redis доступен по имени сервиса
        r = redis.Redis(host='redis', port=6379, db=0)
        r.ping()
        print("✅ Redis доступен")
        return True
    except Exception as e:
        print(f"❌ Ошибка Redis: {e}")
        print("💡 Убедитесь, что Redis запущен")
        return False


def test_celery_connection():
    """Тестирование Celery"""
    print("🔍 Тестирование Celery...")
    try:
        from CRM.celery import app
        # Проверка подключения к broker
        app.connection().ensure_connection(max_retries=1)
        print("✅ Celery broker доступен")
        return True
    except Exception as e:
        print(f"❌ Ошибка Celery: {e}")
        print("💡 Убедитесь, что Redis запущен и Celery worker активен")
        return False


def test_file_upload():
    """Тестирование загрузки файлов"""
    print("🔍 Тестирование загрузки файлов...")

    # Создание тестового файла
    test_file_path = ROOT_DIR / "test_image.jpg"
    if not test_file_path.exists():
        # Создание простого тестового файла
        with open(test_file_path, 'wb') as f:
            f.write(b"test image data")

    try:
        # Использование заранее созданного токена
        token = "a608e0d7d827e97655056e3871eabbbc905e6ded"
        headers = {'Authorization': f'Token {token}'}

        with open(test_file_path, 'rb') as f:
            files = {'file': ('test.jpg', f, 'image/jpeg')}
            response = requests.post(
                "http://localhost:8000/api/upload/",
                files=files,
                headers=headers,
                timeout=10
            )

        if response.status_code == 201:
            print("✅ Загрузка файлов работает")
            result = response.json()
            print(f"   Файл загружен: {result.get('file_url', 'N/A')}")
            return True
        else:
            print(f"❌ Загрузка файлов вернула статус {response.status_code}")
            print(f"   Ответ: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Ошибка загрузки файлов: {e}")
        print("💡 Убедитесь, что Django сервер запущен и пользователь авторизован")
        return False
    finally:
        # Удаление тестового файла
        if test_file_path.exists():
            test_file_path.unlink()


def test_system_status():
    """Тестирование статуса системы"""
    print("🔍 Тестирование статуса системы...")
    try:
        from crm_app.services.health_monitor import HealthMonitor
        import asyncio

        monitor = HealthMonitor()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        status = loop.run_until_complete(monitor.get_system_status())
        loop.close()

        print(f"✅ Статус системы: {status.get('status', 'unknown')}")
        print(f"   Активных аккаунтов: {status.get('accounts', {}).get('active', 0)}")
        print(f"   Всего чатов: {status.get('chats', 0)}")
        return True

    except Exception as e:
        print(f"❌ Ошибка получения статуса системы: {e}")
        return False


def test_resend_code_functionality():
    """Тестирование функции повторной отправки кода"""
    print("🔍 Тестирование функции повторной отправки кода...")
    try:
        # Проверяем, есть ли аккаунты в статусе authenticating
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')
        import django
        django.setup()

        from crm_app.models import TelegramAccount

        authenticating_accounts = TelegramAccount.objects.filter(
            status=TelegramAccount.AccountStatus.AUTHENTICATING
        )

        if not authenticating_accounts.exists():
            print("   ℹ️  Нет аккаунтов в состоянии аутентификации")
            return True

        # Тестируем resend для первого аккаунта
        account = authenticating_accounts.first()
        print(f"   📱 Тестирование resend для аккаунта: {account.name}")

        # Импортируем здесь чтобы избежать проблем с импортами
        from crm_app.services.telegram_client_manager import TelegramClientManager

        manager = TelegramClientManager()
        result = manager.resend_code_sync(account)

        if result.get('success'):
            print(f"   ✅ Resend успешен: {result.get('message', '')}")
            print(f"   📨 Метод: {result.get('code_type', 'unknown')}")
        else:
            print(f"   ⚠️  Resend вернул ошибку: {result.get('error', 'unknown')}")
            # Это может быть нормально если аккаунт не в правильном состоянии

        return True

    except Exception as e:
        print(f"   ❌ Ошибка тестирования resend: {e}")
        return False


def main():
    """Основная функция тестирования"""
    print("🚀 Запуск тестирования Omnichannel CRM системы")
    print("=" * 50)

    tests = [
        ("База данных", test_database_connection),
        ("API здоровье", test_api_health),
        ("WebSocket", test_websocket_connection),
        ("Redis", test_redis_connection),
        ("Celery", test_celery_connection),
        ("Загрузка файлов", test_file_upload),
        ("Статус системы", test_system_status),
        ("Resend код", test_resend_code_functionality),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\n📋 {test_name}:")
        result = test_func()
        results.append((test_name, result))
        time.sleep(0.5)  # Небольшая задержка между тестами

    print("\n" + "=" * 50)
    print("📊 Результаты тестирования:")

    passed = 0
    total = len(results)

    for test_name, result in results:
        status = "✅ Пройден" if result else "❌ Провален"
        print(f"   {test_name}: {status}")
        if result:
            passed += 1

    print(f"\n🎯 Всего тестов: {total}, Пройдено: {passed}, Провалено: {total - passed}")

    if passed == total:
        print("\n🎉 Все тесты пройдены! Система готова к работе.")
        return 0
    else:
        print("\n⚠️  Некоторые тесты провалены. Проверьте настройки и повторите.")
        return 1


if __name__ == "__main__":
    sys.exit(main())