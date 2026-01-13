# Архитектура Omnichannel CRM System

## Обзор системы

Omnichannel CRM система построена на Django и предназначена для управления множественными Telegram источниками (личные аккаунты и боты) через единый веб-интерфейс.

## Основные компоненты

### 1. Модели базы данных (crm_app/models.py)

#### TelegramAccount
- Хранит информацию о Telegram аккаунтах (личные и боты)
- Поддерживает два типа: `PERSONAL` (Hydrogram) и `BOT` (pyTelegramBotAPI)
- Сохраняет session strings для Hydrogram клиентов
- Хранит bot tokens для ботов
- Статусы: `ACTIVE`, `INACTIVE`, `AUTHENTICATING`, `ERROR`

#### Chat
- Представляет чат/диалог в Telegram
- Связан с TelegramAccount (один аккаунт может иметь много чатов)
- Типы: `PRIVATE`, `GROUP`, `SUPERGROUP`, `CHANNEL`
- Индексирован по `telegram_id`, `telegram_account`, `last_message_at`

#### Message
- Хранит все сообщения из чатов
- Типы: `TEXT`, `PHOTO`, `VIDEO`, `VOICE`, `DOCUMENT`, и т.д.
- Статусы: `RECEIVED`, `SENT`, `PENDING`, `FAILED`
- Индексирован для быстрого поиска по чату и дате
- Поддерживает ответы (reply_to_message)

#### Operator
- Расширенная модель пользователя для операторов
- Связан с Django User моделью
- Хранит лимиты чатов (`max_chats`, `current_chats`)

#### ChatAssignment
- Multi-tenant модель для назначения чатов операторам
- Один чат может быть назначен одному оператору
- Поддерживает историю назначений

### 2. Сервисный слой

#### TelegramClientManager (crm_app/services/telegram_client_manager.py)

Singleton менеджер для управления множественными Hydrogram клиентами.

**Основные функции**:
- `start_client(account)` - Запуск Hydrogram клиента для аккаунта
- `stop_client(account_id)` - Остановка клиента
- `restart_client(account_id)` - Перезапуск клиента
- `send_message(account_id, chat_id, text)` - Отправка сообщения через Hydrogram
- `start_all_active()` - Запуск всех активных аккаунтов
- `stop_all()` - Остановка всех клиентов

**Обработка ошибок**:
- `FloodWait` - автоматическое ожидание и retry
- `AuthKeyUnregistered` - обновление статуса аккаунта
- `UserDeactivated` - обработка деактивированных аккаунтов

**Асинхронная обработка сообщений**:
- Обработчики сообщений создаются для каждого клиента
- Входящие сообщения обрабатываются через Celery задачи
- Использует `database_sync_to_async` для работы с Django ORM

#### MessageRouter (crm_app/services/message_router.py)

Роутер для автоматического определения способа отправки ответов.

**Логика маршрутизации**:
1. Определяет тип аккаунта (Personal/Bot)
2. Для Personal аккаунтов: использует TelegramClientManager
3. Для ботов: использует Bot API через requests

**Методы**:
- `send_reply_async(message, text, media_path)` - Асинхронная отправка
- `send_reply(message, text, media_path)` - Синхронная обёртка
- `create_outgoing_message()` - Создание записи об отправленном сообщении

### 3. REST API (crm_app/views.py, serializers.py)

#### Endpoints

**TelegramAccountViewSet** (`/api/accounts/`):
- `GET /api/accounts/` - Список аккаунтов
- `POST /api/accounts/` - Создание аккаунта
- `POST /api/accounts/{id}/start/` - Запуск клиента
- `POST /api/accounts/{id}/stop/` - Остановка клиента
- `POST /api/accounts/{id}/restart/` - Перезапуск клиента
- `POST /api/accounts/authenticate/` - Начало авторизации
- `POST /api/accounts/{id}/verify_otp/` - Подтверждение OTP

**ChatViewSet** (`/api/chats/`):
- `GET /api/chats/` - Список чатов (только назначенные оператору)
- `POST /api/chats/{id}/assign/` - Назначить чат оператору
- `POST /api/chats/{id}/unassign/` - Снять назначение

**MessageViewSet** (`/api/messages/`):
- `GET /api/messages/` - Список сообщений
- `GET /api/messages/by_chat/?chat_id=1` - Сообщения чата
- `POST /api/messages/{id}/reply/` - Отправить ответ

**BotWebhookView** (`/api/webhook/bot/`):
- `POST /api/webhook/bot/?token=BOT_TOKEN` - Webhook от бота
- Принимает стандартный Update объект от Telegram Bot API
- Обрабатывает сообщения и создает записи в БД

### 4. Django Channels (WebSockets)

#### MessageConsumer (crm_app/consumers.py)

WebSocket consumer для real-time обновлений.

**Группы**:
- Каждый оператор подписывается на группу `operator_{user_id}`
- Обновления отправляются только операторам с назначенными чатами

**События от клиента**:
- `get_chat_messages` - Получить сообщения чата
- `mark_as_read` - Отметить чат как прочитанный

**События от сервера**:
- `initial_chats` - Начальный список чатов
- `new_message` - Новое сообщение
- `chat_updated` - Обновление чата
- `chat_marked_as_read` - Чат отмечен как прочитанный

**Маршрутизация** (`crm_app/routing.py`):
- `/ws/messages/` - WebSocket endpoint для сообщений

### 5. Celery задачи (crm_app/tasks.py)

#### process_incoming_message

Асинхронная обработка входящих сообщений.

**Функциональность**:
- Получение или создание чата
- Создание записи сообщения в БД
- Поиск сообщения на которое отвечают
- Запуск задачи загрузки медиа (если есть)

**Retry логика**:
- Максимум 3 попытки
- Интервал между попытками: 60 секунд

#### download_media

Асинхронная загрузка медиа файлов из Telegram.

**Для ботов**:
- Использует Bot API `getFile` метод
- Скачивает файл и сохраняет локально
- Обновляет путь к файлу в сообщении

**Для Hydrogram**:
- Медиа обрабатывается в обработчике сообщений
- TODO: Реализовать загрузку через Hydrogram клиент

#### cleanup_old_messages

Периодическая задача для очистки старых сообщений.
- Запускается ежедневно через Celery Beat
- Удаляет сообщения старше 90 дней

### 6. Конфигурация Django (CRM/settings.py)

#### База данных MySQL

**Оптимизации**:
- `charset: utf8mb4` - Поддержка эмодзи
- `isolation_level: read_committed` - Уменьшение deadlocks
- `CONN_MAX_AGE: 300` - Переиспользование соединений
- Buffer pool: 1GB
- Max connections: 500

**Индексы**:
- Составные индексы для частых запросов
- Индексы на внешние ключи
- Индексы на даты для сортировки

#### Django Channels

**Channel Layer**: Redis
- `capacity: 1500` - Максимум сообщений в канале
- `expiry: 10` - Время жизни сообщения

#### Celery

**Конфигурация**:
- `BROKER_URL: redis://localhost:6379/0`
- `RESULT_BACKEND: redis://localhost:6379/0`
- `TASK_ACKS_LATE: True` - Подтверждение после выполнения
- `WORKER_PREFETCH_MULTIPLIER: 1` - Лучшая балансировка
- `CONCURRENCY: 4` - 4 worker процесса
- `MAX_TASKS_PER_CHILD: 1000` - Предотвращение утечек памяти

### 7. Docker Compose

**Сервисы**:
- `db` - MySQL 8.0
- `redis` - Redis 7
- `web` - Django приложение (HTTP)
- `daphne` - Django Channels (WebSockets)
- `celery_worker` - Celery worker для фоновых задач
- `celery_beat` - Celery beat для периодических задач

**Volumes**:
- `mysql_data` - Данные MySQL
- `redis_data` - Данные Redis
- `static_volume` - Статические файлы
- `media_volume` - Медиа файлы
- `sessions_volume` - Сессии Hydrogram
- `logs_volume` - Логи

## Потоки данных

### Входящее сообщение (Personal Account)

1. Hydrogram клиент получает сообщение
2. Обработчик сообщений (`handle_message`) вызывается
3. Чат создается или получается из БД (async операция)
4. Celery задача `process_incoming_message` запускается
5. Сообщение сохраняется в БД
6. Если есть медиа, запускается задача `download_media`
7. WebSocket уведомление отправляется операторам с назначенными чатами

### Входящее сообщение (Bot)

1. Бот получает обновление через Telegram Bot API
2. Обновление отправляется на `/api/webhook/bot/`
3. WebhookView обрабатывает update
4. Чат создается или получается из БД
5. Celery задача `process_incoming_message` запускается
6. Сообщение сохраняется в БД
7. Если есть медиа, запускается задача `download_media`
8. WebSocket уведомление отправляется операторам

### Исходящее сообщение (Ответ оператора)

1. Оператор отправляет ответ через REST API (`/api/messages/{id}/reply/`)
2. ViewSet проверяет, что чат назначен оператору
3. MessageRouter определяет способ отправки
4. Для Personal: используется TelegramClientManager
5. Для Bot: используется Bot API через requests
6. Сообщение отправляется в Telegram
7. Запись об отправленном сообщении создается в БД
8. WebSocket уведомление отправляется оператору

## Безопасность

### Аутентификация
- REST API: Token Authentication (DRF)
- WebSockets: Session Authentication (Channels AuthMiddleware)

### Авторизация
- Multi-tenant: операторы видят только назначенные чаты
- Проверка прав доступа в ViewSets
- Проверка прав в WebSocket Consumer

### Защита данных
- Session strings хранятся в БД (зашифрованы в production)
- Bot tokens хранятся в БД (зашифрованы в production)
- CORS настройки для фронтенда
- CSRF защита для форм

## Производительность

### Оптимизации MySQL
- Составные индексы для сложных запросов
- READ-COMMITTED isolation level
- Buffer pool размером 1GB
- Connection pooling (CONN_MAX_AGE)

### Оптимизации Celery
- 4 worker процесса для параллельной обработки
- Prefetch multiplier = 1 для лучшей балансировки
- Max tasks per child = 1000 для предотвращения утечек памяти

### Оптимизации Redis
- Max memory: 512MB
- Eviction policy: allkeys-lru

### Кеширование
- Redis для Channel Layer
- Connection pooling для MySQL
- Оптимизированные запросы с select_related/prefetch_related

## Масштабирование

### Горизонтальное масштабирование
- Несколько Django приложений за Nginx load balancer
- Несколько Celery workers
- Redis Cluster для высокой доступности
- MySQL Master-Slave репликация

### Вертикальное масштабирование
- Увеличение buffer pool MySQL
- Больше worker процессов Celery
- Больше памяти для Redis

## Мониторинг

### Логирование
- Django logs: `logs/crm.log`
- Celery logs: `logs/celery_worker.log`, `logs/celery_beat.log`
- WebSocket logs: в Django logs

### Метрики
- Количество активных клиентов
- Количество обработанных сообщений
- Время выполнения задач Celery
- Размер БД и таблиц
- Использование памяти и CPU

## Резервное копирование

### База данных
- Ежедневные дампы MySQL
- Хранение бэкапов на внешнем хранилище
- Автоматическое восстановление при сбоях

### Сессии Hydrogram
- Регулярное архивирование сессий
- Хранение бэкапов сессий

## Будущие улучшения

1. **Графан и Prometheus** для мониторинга
2. **Sentry** для отслеживания ошибок
3. **Elasticsearch** для поиска по сообщениям
4. **PostgreSQL** вместо MySQL для лучшей производительности
5. **Kubernetes** для оркестрации
6. **gRPC** для внутренней коммуникации сервисов
7. **WebRTC** для голосовых/видео звонков
8. **AI/ML** для автоматизации ответов
