# Руководство по установке и настройке

## Быстрый старт

### 1. Установка зависимостей

```bash
# Создание виртуального окружения
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

# Установка зависимостей
pip install -r requirements.txt
```

**Примечание**: Для установки `mysqlclient` может потребоваться установка системных библиотек:

#### Linux (Ubuntu/Debian)
```bash
sudo apt-get update
sudo apt-get install default-libmysqlclient-dev build-essential python3-dev
```

#### macOS
```bash
brew install mysql-client
export PATH="/usr/local/opt/mysql-client/bin:$PATH"
```

#### Windows
Используйте альтернативный драйвер PyMySQL:
```bash
# В requirements.txt замените mysqlclient на PyMySQL
# pip install PyMySQL
```

### 2. Настройка базы данных

База данных создается автоматически при запуске Docker Compose:

```bash
# Запуск MySQL и Redis (база создается автоматически)
docker-compose up -d db redis

# Ожидание готовности БД
docker-compose logs -f db
```

**Как это работает:**
- MySQL контейнер использует переменные окружения `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`
- При первом запуске автоматически создается база данных и пользователь
- Скрипт `docker/mysql/init.sql` выполняется для дополнительной настройки (оптимизация производительности, кодировка utf8mb4)

**Если используете локальную установку MySQL (без Docker):**

```bash
# Установка MySQL (пример для Ubuntu)
sudo apt-get install mysql-server

# Создание базы данных вручную
mysql -u root -p
```

```sql
CREATE DATABASE omnichannel_crm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'crm_user'@'localhost' IDENTIFIED BY 'crm_password';
GRANT ALL PRIVILEGES ON omnichannel_crm.* TO 'crm_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 3. Настройка переменных окружения

Создайте файл `.env` в корне проекта:

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
# Django
SECRET_KEY=your-secret-key-here-generate-with-openssl-rand-hex-32
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# База данных MySQL
DB_NAME=omnichannel_crm
DB_USER=crm_user
DB_PASSWORD=crm_password
DB_HOST=localhost
DB_PORT=3306

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# CORS (для фронтенда)
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

### 4. Применение миграций

```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. Создание суперпользователя

```bash
python manage.py createsuperuser
```

### 6. Запуск приложения

#### Локальная разработка

**Терминал 1 - Django сервер**:
```bash
python manage.py runserver
```

**Терминал 2 - Celery Worker**:
```bash
celery -A CRM worker --loglevel=info --concurrency=4
```

**Терминал 3 - Celery Beat**:
```bash
celery -A CRM beat --loglevel=info
```

**Терминал 4 - Daphne (WebSockets)**:
```bash
daphne -b 0.0.0.0 -p 8001 CRM.asgi:application
```

#### Docker Compose (рекомендуется)

```bash
# Запуск всех сервисов
docker-compose up -d

# Просмотр логов
docker-compose logs -f

# Остановка
docker-compose down

# Пересборка образов
docker-compose build
docker-compose up -d
```

## Настройка Telegram аккаунтов

### 1. Получение API credentials

1. Перейдите на https://my.telegram.org/apps
2. Войдите с вашим номером телефона
3. Создайте новое приложение
4. Сохраните `api_id` и `api_hash`

### 2. Добавление личного аккаунта через API

```bash
# Начало авторизации
curl -X POST http://localhost:8000/api/accounts/authenticate/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+1234567890",
    "api_id": 12345,
    "api_hash": "your_api_hash_here"
  }'

# Получение OTP кода в Telegram
# Подтверждение OTP
curl -X POST http://localhost:8000/api/accounts/{account_id}/verify_otp/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "otp_code": "12345"
  }'

# Запуск клиента
curl -X POST http://localhost:8000/api/accounts/{account_id}/start/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### 3. Добавление бота

```bash
curl -X POST http://localhost:8000/api/accounts/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "account_type": "bot",
    "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    "bot_username": "my_bot"
  }'
```

### 4. Настройка webhook для существующего бота

В вашем существующем боте (pyTelegramBotAPI):

```python
import requests

# Настройка webhook на CRM

# Отправка обновлений в CRM
def forward_to_crm(update):
    response = requests.post(
        json=update.to_dict(),
        headers={"X-Bot-Token": "BOT_TOKEN"}
    )
    return response.status_code == 200
```

Или используйте middleware в вашем боте:

```python
import telebot
from telebot import apihelper

# Middleware для пересылки обновлений в CRM
def crm_middleware(message):
    if message:
        forward_to_crm(message)
    return True

bot = telebot.TeleBot("BOT_TOKEN")
bot.set_update_listener(crm_middleware)
```

## Управление через команды Django

### Запуск всех аккаунтов

```bash
python manage.py start_telegram_accounts
```

### Остановка всех аккаунтов

```bash
python manage.py stop_telegram_accounts
```

## Проверка работы системы

### 1. Проверка API

```bash
# Получение списка аккаунтов
curl -X GET http://localhost:8000/api/accounts/ \
  -H "Authorization: Token YOUR_TOKEN"

# Получение чатов
curl -X GET http://localhost:8000/api/chats/ \
  -H "Authorization: Token YOUR_TOKEN"

# Получение сообщений
curl -X GET http://localhost:8000/api/messages/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### 2. Проверка WebSocket

```javascript
// В браузерной консоли или Node.js
const ws = new WebSocket('ws://localhost:8001/ws/messages/');

ws.onopen = () => {
    console.log('WebSocket connected');
    // Получение начальных чатов
    ws.send(JSON.stringify({type: 'get_chat_messages', chat_id: 1}));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};

ws.onclose = () => {
    console.log('WebSocket closed');
};
```

### 3. Проверка Celery

```bash
# Проверка статуса worker
celery -A CRM inspect active

# Проверка зарегистрированных задач
celery -A CRM inspect registered

# Мониторинг задач
celery -A CRM events
```

## Решение проблем

### Проблема: MySQL не подключается

**Решение**:
1. Проверьте, что MySQL запущен: `systemctl status mysql`
2. Проверьте credentials в `.env`
3. Проверьте, что пользователь имеет права доступа

### Проблема: Redis connection error

**Решение**:
1. Проверьте, что Redis запущен: `redis-cli ping`
2. Проверьте `REDIS_HOST` и `REDIS_PORT` в `.env`

### Проблема: Telethon клиент не запускается

**Решение**:
1. Проверьте, что session_string сохранен в БД
2. Проверьте, что api_id и api_hash правильные
3. Проверьте логи: `tail -f logs/crm.log`

### Проблема: WebSocket не подключается

**Решение**:
1. Проверьте, что Daphne запущен на порту 8001
2. Проверьте CORS настройки
3. Проверьте, что пользователь авторизован

### Проблема: Celery задачи не выполняются

**Решение**:
1. Проверьте, что Celery worker запущен
2. Проверьте подключение к Redis
3. Проверьте логи worker: `celery -A CRM worker --loglevel=debug`

## Production deployment

### 1. Настройка production settings

```env
DEBUG=False
ALLOWED_HOSTS=your-domain.com,www.your-domain.com
SECRET_KEY=generate-secure-key-here
```

### 2. Использование Nginx

```nginx
# /etc/nginx/sites-available/omnichannel-crm
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /ws/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
    
    location /static/ {
        alias /path/to/omnichannel-crm/staticfiles/;
    }
    
    location /media/ {
        alias /path/to/omnichannel-crm/media/;
    }
}
```

### 3. Использование systemd для сервисов

Создайте файлы служб для автоматического запуска:

```ini
# /etc/systemd/system/omnichannel-crm.service
[Unit]
Description=Omnichannel CRM Django
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/omnichannel-crm
ExecStart=/path/to/venv/bin/gunicorn CRM.wsgi:application --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

## Мониторинг и логи

### Логи Django

```bash
tail -f logs/crm.log
```

### Логи Celery

```bash
# Worker
tail -f logs/celery_worker.log

# Beat
tail -f logs/celery_beat.log
```

### Мониторинг БД

```bash
# Подключение к MySQL
mysql -u crm_user -p omnichannel_crm

# Проверка размера таблиц
SELECT table_name, 
       ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb
FROM information_schema.TABLES
WHERE table_schema = 'omnichannel_crm'
ORDER BY size_mb DESC;
```

## Резервное копирование

### База данных

```bash
# Создание бэкапа
mysqldump -u crm_user -p omnichannel_crm > backup_$(date +%Y%m%d_%H%M%S).sql

# Восстановление
mysql -u crm_user -p omnichannel_crm < backup_20240101_120000.sql
```

### Сессии Telethon

```bash
# Архивирование сессий
tar -czf sessions_backup_$(date +%Y%m%d).tar.gz sessions/
```

## Дополнительные ресурсы

- [Django Documentation](https://docs.djangoproject.com/)
- [Django REST Framework](https://www.django-rest-framework.org/)
- [Django Channels](https://channels.readthedocs.io/)
- [Celery Documentation](https://docs.celeryproject.org/)
- [Telethon Documentation](https://docs.telethon.dev/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
