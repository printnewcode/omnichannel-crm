#!/usr/bin/env python
"""
Тестовый скрипт для проверки альтернативных методов верификации OTP
Полезно когда SMS не приходит (особенно в России)
"""
import os
import sys
import requests

# Добавление корневой директории в путь
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

def test_resend_functionality():
    """Тестирование функции повторной отправки кода"""
    print("🔄 Тестирование функции повторной отправки OTP кода")
    print("=" * 60)

    # Замените на реальные значения
    ACCOUNT_ID = 1
    AUTH_TOKEN = "a608e0d7d827e97655056e3871eabbbc905e6ded"  # Получите из админки

    headers = {
        'Authorization': f'Token {AUTH_TOKEN}',
        'Content-Type': 'application/json'
    }

    print(f"📱 Тестирование аккаунта ID: {ACCOUNT_ID}")
    print(f"🔑 Используемый токен: {AUTH_TOKEN[:10]}...")
    print()

    # 1. Проверить статус аккаунта
    print("1️⃣ Проверка статуса аккаунта:")
    try:
        response = requests.get(
            f"http://localhost:8000/api/accounts/{ACCOUNT_ID}/",
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:
            account_data = response.json()
            status = account_data.get('status')
            print(f"   ✅ Статус аккаунта: {status}")

            if status == 'authenticating':
                print("   ✅ Аккаунт готов для повторной отправки кода")
            else:
                print(f"   ⚠️  Аккаунт в статусе '{status}'. Сначала запустите аутентификацию.")
                return
        else:
            print(f"   ❌ Ошибка получения статуса: {response.status_code}")
            return

    except requests.RequestException as e:
        print(f"   ❌ Ошибка подключения: {e}")
        print("   💡 Убедитесь, что Django сервер запущен: docker-compose up -d")
        return

    print()

    # 2. Тест повторной отправки кода
    print("2️⃣ Тестирование повторной отправки кода:")
    try:
        response = requests.post(
            f"http://localhost:8000/api/accounts/{ACCOUNT_ID}/resend_code/",
            headers=headers,
            timeout=30  # Дольше, так как может быть звонок
        )

        if response.status_code == 200:
            result = response.json()
            print("   ✅ Код отправлен повторно!"            print(f"   📨 Метод: {result.get('code_type', 'unknown')}")
            print(f"   💬 Сообщение: {result.get('message', '')}")

            if result.get('next_type'):
                print(f"   🔄 Следующий метод: {result.get('next_type')}")

            print()
            print("🎯 Проверьте:")
            print("   • Telegram приложение на этом устройстве")
            print("   • Входящие звонки")
            print("   • Пропущенные звонки (код в номере)")
            print("   • Email (если подключен)")

        elif response.status_code == 400:
            error_data = response.json()
            print(f"   ❌ Ошибка: {error_data.get('error', 'Неизвестная ошибка')}")
        else:
            print(f"   ❌ HTTP ошибка: {response.status_code}")
            print(f"   📄 Ответ: {response.text}")

    except requests.RequestException as e:
        print(f"   ❌ Ошибка подключения: {e}")
        return

    print()
    print("=" * 60)
    print("📚 Доступные методы верификации Telegram:")
    print("   📱 APP - Уведомление в Telegram приложении")
    print("   📞 CALL - Звонок с кодом")
    print("   📞 FLASH_CALL - Пропущенный звонок (код в номере)")
    print("   📧 EMAIL_CODE - Код на email")
    print("   📨 FRAGMENT_SMS - Альтернативная SMS")
    print()
    print("🇷🇺 В России SMS часто не доставляется.")
    print("💡 Используйте функцию повторной отправки для смены метода!")

if __name__ == "__main__":
    print("🚀 Тест альтернативных методов верификации OTP")
    print("💡 Этот скрипт поможет если SMS не приходит")
    print()

    test_resend_functionality()