# Omnichannel CRM System

Omnichannel CRM-система для сбора сообщений с разных мессенджеров (Telegram) и ответа с одного WEB-сайта.

## Технический стек

- **Backend**: Python 3.12+, Django 5.1+, Django REST Framework
- **Database**: MySQL 8.0+ (оптимизировано для высоконагруженных записей)
- **Telegram**: Telethon (личные аккаунты) + Bot API (боты)
- **Real-time**: Django Channels + WebSockets
- **Background Tasks**: Celery + Redis
- **Deployment**: Docker Compose

## Основные возможности

- ✅ Управление несколькими личными Telegram аккаунтами через Telethon
- ✅ Интеграция с существующими ботами через webhook
- ✅ Единый веб-интерфейс для всех чатов
- ✅ Real-time обновления через WebSockets
- ✅ Multi-tenant система (операторы видят только назначенные чаты)
- ✅ Автоматическая загрузка медиа файлов
- ✅ Система мониторинга здоровья

## Быстрый старт

### Docker Compose (рекомендуется)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/printnewcode/omnichannel-crm.git
cd omnichannel-crm

# 2. Запустить все сервисы
docker-compose up -d

# 3. Применить миграции
docker-compose exec web python manage.py migrate

# 4. Создать суперпользователя
docker-compose exec web python manage.py createsuperuser

# 5. Открыть админку
# http://localhost:8000/admin/
```

### Структура проекта

```
omnichannel-crm/
├── CRM/                    # Основной проект Django
├── crm_app/               # Основное приложение
│   ├── models.py          # Модели БД
│   ├── views.py           # REST API
│   ├── services/          # Бизнес-логика
│   └── ...
├── docker-compose.yml     # Docker конфигурация
└── requirements.txt       # Python зависимости
```

## Использование

### Добавление Telegram аккаунта

1. **Перейдите в админку** `http://localhost:8000/admin/`
2. **Добавьте аккаунт** в раздел "Telegram Accounts"
3. **Для личного аккаунта** заполните:
   - Name (описательное имя)
   - Account type: "Личный аккаунт (Telethon)"
   - Phone number (+1234567890)
   - API ID и API Hash (с https://my.telegram.org/)
4. **Сохраните и пройдите аутентификацию** через API

### Добавление бота

1. **Получите токен** у @BotFather в Telegram
2. **Добавьте аккаунт** в админку:
   - Name (имя бота)
   - Account type: "Бот (pyTelegramBotAPI)"
   - Bot token (от @BotFather)
   - Bot username (без @)

### Настройка webhook для бота

Для существующего бота настройте webhook на:
```
https://your-domain.com/api/webhook/bot/?token=YOUR_BOT_TOKEN
```

## Тестирование

```bash
# Запуск автоматического тестирования
docker-compose exec web python test_setup.py
```

## Лицензия

Apache-2.0