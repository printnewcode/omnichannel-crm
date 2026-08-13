# Техническая эксплуатация

Этот справочник дополняет [SETUP.md](SETUP.md). Он предназначен для разработчиков и администраторов, которым нужны устройство проекта, полный набор настроек и расширенная диагностика.

## Состав Docker-приложения

| Сервис | Назначение |
| --- | --- |
| `db` | MySQL 8 — основная база CRM |
| `redis` | брокер Celery и транспорт Django Channels |
| `migrate` | применяет миграции и собирает static-файлы перед запуском |
| `web` | Django ASGI/Daphne, REST API, Admin, webhook и WebSocket |
| `connector` | постоянные соединения личных Telegram-аккаунтов через Telethon |
| `worker` | фоновая доставка, скачивание медиа и задачи webhook-провайдеров |

Ручная загрузка прошлых сообщений и диалогов также выполняется в `worker`. Прогресс хранится в таблице `HistoryImportJob`, поэтому HTTP-запрос завершается сразу, а длительная операция не удерживает web-процесс. Telegram в режиме «вся история» читается потоком. Ответ GREEN-API не имеет постраничной выдачи, поэтому одна задача запрашивает не более 10 000 доступных сообщений — это защищает небольшой VPS от большого единовременного ответа.

Telegram читает доступную историю напрямую через Telethon. WhatsApp и MAX используют методы GREEN-API `getChats` и `getChatHistory`; они возвращают только ту историю, которую соответствующий инстанс GREEN-API синхронизировал с личным аккаунтом.

Telethon запускается только в `connector`. Значение `RUN_TELETHON_CLIENTS` в остальных контейнерах должно оставаться `0`, чтобы одна Telegram-сессия не использовалась одновременно несколькими процессами.

Локально MySQL CRM опубликован на `127.0.0.1:3307`, чтобы не конфликтовать с `jget-bot` на `3306`; внутри Docker все сервисы обращаются к `db:3306`.

## Переменные окружения

Базовый локальный пример:

```dotenv
DEBUG=True
LOCAL=False
SECRET_KEY=replace-with-a-long-random-value
ALLOWED_HOSTS=127.0.0.1,localhost
DOMAIN=
CORS_ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

DB_NAME=omnichannel
DB_USER=omnichannel
DB_PASSWORD=replace-database-password
MYSQL_ROOT_PASSWORD=replace-root-password
DB_HOST=db
DB_PORT=3306

REDIS_URL=redis://redis:6379/0
RUN_TELETHON_CLIENTS=0

SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

`LOCAL=False` включает общую MySQL. `LOCAL=True` использует SQLite и подходит только для упрощенного однопроцессного запуска.

Сгенерировать `SECRET_KEY`:

```bash
python -c "from secrets import token_urlsafe; print(token_urlsafe(64))"
```

Секреты, токены провайдеров, `api_hash`, `StringSession` и `.env` нельзя сохранять в Git.

## Telegram-прокси

Если контейнер не устанавливает прямое MTProto-соединение, укажите доступный из Docker SOCKS5-прокси:

```dotenv
TELEGRAM_PROXY_URL=socks5://host.docker.internal:10808
```

На Linux вместо `host.docker.internal` может потребоваться адрес Docker-host или дополнительная запись `host-gateway` в Compose.

## Хранение данных

Локальный Compose использует тома:

- `mysql_data` — база MySQL;
- `redis_data` — данные Redis;
- `media` — входящие и исходящие файлы CRM;
- `sessions` — файловые Telegram-сессии;
- `staticfiles` — собранная статика;
- `logs` — журналы приложения.

`StringSession` личного Telegram-аккаунта хранится в MySQL. Все процессы, работающие с файлами, должны видеть один и тот же том `media`.

Команда ниже удаляет локальные тома и данные без возможности восстановления из Docker:

```bash
docker compose down -v
```

## Поток сообщений

Личный Telegram обслуживает постоянный Telethon connector. Telegram-бот, WhatsApp и MAX принимают HTTP webhook. После сохранения сообщения CRM публикует WebSocket-событие в интерфейс.

Исходящий ответ сначала записывается в `OutboundDelivery`. Connector или worker забирает его из очереди, выполняет отправку и сохраняет provider message ID. Временная ошибка переводит доставку в повторную попытку.

Браузер получает основные обновления через WebSocket. Редкая HTTP-сверка читает только базу CRM и служит страховкой после переподключения.

### Вложения

Одна операция интерфейса принимает до 10 файлов. CRM сохраняет каждое вложение как отдельный элемент надежной исходящей очереди: текст становится подписью первого файла, остальные отправляются без повторения подписи. Такой формат одинаково работает для Telegram, WhatsApp и MAX; фактическое объединение файлов в один provider album зависит от возможностей мессенджера и GREEN-API.

Расширения файлов не ограничиваются. Имя файла сохраняется в отдельном UUID-каталоге, поэтому провайдер получает исходное имя вместо hash. SVG и прочие потенциально активные форматы отображаются как документы, а не как inline-изображения. Общий лимит одного файла — 100 МБ; более низкий внешний лимит провайдера возвращается как ошибка доставки.

Входящие Telegram-медиа скачивает постоянный `connector` через уже авторизованное соединение. Входящие WhatsApp/MAX-медиа скачивает `worker` по проверенному GREEN-API `downloadUrl`. Исходное имя хранится в metadata сообщения и возвращается API полем `media_file_name`.

## Особенности каналов

### Telegram personal

Аккаунту нужны `api_id`, `api_hash` и действительная `StringSession`. Connector периодически сверяет список активных аккаунтов и может подхватить новый аккаунт без перезапуска `web`.

Действия «Запустить», «Остановить» и «Перезапустить» в Admin не открывают MTProto-соединение в web. Они записывают желаемое состояние в MySQL, а connector применяет его при ближайшей сверке (по умолчанию не позднее 5 секунд). Проверка Telegram-сессий при простом открытии списка аккаунтов не выполняется.

### Telegram bot bridge

CRM принимает обращения по подписанному endpoint и отправляет ответы в `bridge_url`. Формат подписей и payload описан в [EXISTING_BOT_INTEGRATION.md](EXISTING_BOT_INTEGRATION.md).

### WhatsApp GREEN-API

CRM использует:

- `sendMessage` для текста;
- `sendFileByUpload` для файлов;
- `setSettings` для webhook;
- `incomingMessageReceived` для входящих сообщений;
- `outgoingMessageStatus` для статусов;
- `stateInstanceChanged` для состояния авторизации.

Webhook CRM:

```text
https://your-domain.example/api/integrations/whatsapp/<account_id>/webhook/
```

Admin формирует этот адрес только из `DOMAIN`. Внутренний адрес Docker или `127.0.0.1` не используется: GREEN-API должен обращаться к CRM по публичному HTTPS. Значения `ALLOWED_HOSTS` автоматически очищаются от случайно добавленной схемы, но в `.env` их рекомендуется указывать без `https://`.

CRM проверяет `Authorization` через `green_webhook_token` и сверяет `idInstance`. В Admin действие настройки webhook включает `incomingWebhook`, `outgoingWebhook`, `outgoingAPIMessageWebhook` и `stateWebhook`.

Входящие `downloadUrl` скачиваются worker потоково, с проверкой HTTPS-хоста и лимитом 100 МБ. Помимо API-доменов GREEN-API разрешены только известные кластерные хранилища провайдера: `do-media-<cluster>.<region>.digitaloceanspaces.com` для WhatsApp и `(sw-)media-<cluster>.storage.yandexcloud.net` для MAX. Произвольные домены DigitalOcean/Yandex Object Storage остаются запрещены. Идентификатор `@c.us` или `@g.us` сохраняется в metadata чата, чтобы ответы возвращались в правильный личный или групповой чат.

### MAX GREEN-API

Личный MAX использует тот же транспорт GREEN-API, что и WhatsApp, но отдельный MAX-инстанс и аккаунт CRM типа `max`. Входящие события поступают на `/api/integrations/max/<account_id>/webhook/`, где проверяются `Authorization` и `idInstance`. Для MAX числовой `chatId` хранится и отправляется без суффикса `@c.us`. Текст, файлы, цитирование, статусы доставки и состояние инстанса обрабатываются общей GREEN-API логикой.

Устаревшие provider-поля сохранены только на уровне схемы базы ради безопасного обновления существующих установок; API, Admin и рабочая логика их не используют.
### Общая очередь, фильтрация и архив

REST API и WebSocket используют одну очередь для всех авторизованных операторов. `ChatAssignment` сохранен только как совместимая legacy-модель и больше не участвует в выдаче, отправке, realtime или загрузке входящих сообщений.

Допустимые источники:

- Telegram Telethon/Bot API: `private`, `group`, `supergroup`; broadcast-каналы исключаются и в event handler, и при history catch-up;
- WhatsApp GREEN-API: только `@c.us` и `@g.us`; `@broadcast`, newsletter и другие служебные источники игнорируются;
- MAX GREEN-API: только `senderData.chatType=user|group`; `channel` и `bot` игнорируются.

Поле `Chat.is_archived` является общим для CRM. `POST /api/chats/<id>/archive/` и `unarchive/` переключают состояние. Ответ на сообщение создается через `POST /api/messages/<id>/reply/`; outbox хранит `reply_to_message`, а адаптер передает Telegram `reply_to` либо GREEN-API `quotedMessageId`.
## Ручной запуск без Docker

Ручной режим требует отдельно запущенных MySQL/SQLite, Redis и четырех процессов.

Web:

```bash
python manage.py runserver
```

Telegram connector:

```bash
python manage.py start_telegram_accounts --reconcile-interval 300
```

Celery на Linux:

```bash
celery -A CRM worker --loglevel=INFO
```

Celery на Windows:

```powershell
celery -A CRM worker --loglevel=INFO --pool=solo
```

Для полноценной проверки событийной модели рекомендуется Docker Compose.

## Служебные команды

Применить миграции:

```bash
docker compose run --rm migrate
```

Проверить Django:

```bash
docker compose exec web python manage.py check
```

Проверить отсутствие незаписанной миграции:

```bash
docker compose exec web python manage.py makemigrations --check --dry-run
```

Запустить тесты:

```bash
docker compose exec web python manage.py test
```

Последние логи конкретного сервиса:

```bash
docker compose logs --tail 200 web
docker compose logs --tail 200 connector
docker compose logs --tail 200 worker
```

## Диагностика

### MySQL: `Access denied`

Проверьте совпадение `DB_USER`, `DB_PASSWORD` и `MYSQL_ROOT_PASSWORD`. MySQL применяет стартовые пароли только при первом создании тома. Изменение `.env` не меняет пароль внутри уже существующей базы.

Если данные не нужны, том можно пересоздать командой `docker compose down -v`. Если данные нужны, измените пароль внутри MySQL или восстановите правильное старое значение `.env`.

### Интерфейс обновляется только после перезагрузки

Проверьте `redis`, `REDIS_URL` и проксирование `/ws/`. Для production Nginx должен передавать WebSocket Upgrade-заголовки.

### Telegram personal не получает сообщения

Проверьте `connector`, статус аккаунта, `StringSession` и доступ контейнера к Telegram. Не запускайте второй connector для той же сессии.

### QR Telegram не создается

Проверьте MTProto-доступ из контейнера и `TELEGRAM_PROXY_URL`. Ошибка `0 bytes read` обычно означает обрыв соединения до завершения QR login.

### Ответ остается `pending`

Для личного Telegram проверяйте `connector`. Для Telegram-бота, WhatsApp и MAX проверяйте `worker`, Redis, учетные данные и сетевой доступ к провайдеру.

### GREEN-API не присылает сообщения

Проверьте, что инстанс авторизован, аккаунт CRM активен, webhook имеет публичный HTTPS URL, а `green_webhook_token` совпадает. Повторно выполните действие настройки webhook в Admin после смены домена.

### Локальный HTTPS redirect или cookie

Для HTTP-запуска должны быть выключены `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE` и `CSRF_COOKIE_SECURE`. Production-значения описаны в [VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md).
