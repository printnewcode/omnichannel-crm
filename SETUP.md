# Быстрый запуск и управление

Этот файл содержит только практические действия. Техническое устройство проекта, описание контейнеров, переменных, хранилищ и расширенная диагностика находятся в [TECHNICAL_OPERATIONS.md](TECHNICAL_OPERATIONS.md). Production-развертывание описано в [deploy/README.md](deploy/README.md).

## 1. Что установить

- Docker Desktop для Windows или Docker Engine для Linux;
- Docker Compose v2;
- Git.

Отдельно устанавливать Python, MySQL и Redis для Docker-запуска не требуется.

## 2. Подготовить `.env`

Из корня проекта выполните:

```powershell
Copy-Item .env.example .env
```

На Linux/macOS:

```bash
cp .env.example .env
```

Откройте `.env` и обязательно замените значения:

```dotenv
SECRET_KEY=replace-with-a-long-random-value
DB_PASSWORD=replace-database-password
MYSQL_ROOT_PASSWORD=replace-root-password
```

Для локального запуска оставьте:

```dotenv
DEBUG=True
LOCAL=False
ALLOWED_HOSTS=127.0.0.1,localhost
```

Для WhatsApp или MAX укажите публичный HTTPS-адрес туннеля или домена. В `DOMAIN` нужен полный адрес, а в `ALLOWED_HOSTS` — имя хоста без `https://`:

```dotenv
DOMAIN=https://example.tuna.am
ALLOWED_HOSTS=127.0.0.1,localhost,example.tuna.am
CORS_ALLOWED_ORIGINS=http://127.0.0.1:8000,https://example.tuna.am
```

Не публикуйте `.env` и не передавайте его содержимое вместе с логами.

## 3. Запустить CRM

```bash
docker compose up -d --build
```

Проверьте, что сервисы запущены:

```bash
docker compose ps
```

Откройте:

- CRM: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- Admin: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

## 4. Создать администратора

При первом запуске выполните:

```bash
docker compose exec web python manage.py createsuperuser
```

Войдите с созданными данными в `/admin/`.

## 5. Подключить каналы

Все аккаунты добавляются в Admin в разделе аккаунтов.

### Личный Telegram

1. Получите `api_id` и `api_hash` Telegram на [https://my.telegram.org](https://my.telegram.org).
2. Создайте аккаунт типа `personal`.
3. Заполните имя, телефон, `api_id` и `api_hash`.
4. Пройдите авторизацию кодом или QR в Admin.
5. Установите статус `active`.

Если Telegram из Docker недоступен напрямую, настройте `TELEGRAM_PROXY_URL` по [технической инструкции](TECHNICAL_OPERATIONS.md#telegram-прокси).

### Существующий Telegram-бот

1. Создайте аккаунт типа `bot`.
2. Заполните имя, username бота, `bridge_url` и `bridge_secret`.
3. Установите статус `active`.
4. Настройте передачу сообщений со стороны бота по [EXISTING_BOT_INTEGRATION.md](EXISTING_BOT_INTEGRATION.md).

`account_id` — ID аккаунта в базе CRM, не Telegram ID.

### WhatsApp через GREEN-API

1. Создайте инстанс в GREEN-API и авторизуйте WhatsApp по QR-коду.
2. Создайте аккаунт CRM типа `whatsapp` со статусом `active`.
3. Заполните `green_api_instance_id` и `green_api_token` значениями `idInstance` и `apiTokenInstance`.
4. Придумайте длинный `green_webhook_token`.
5. Оставьте стандартные `green_api_url` и `green_media_url`, если кабинет GREEN-API не выдал другие адреса.
6. Сохраните аккаунт.
7. В списке аккаунтов выберите его и выполните действие «Настроить webhook выбранных WhatsApp-аккаунтов в GREEN-API».
8. Отправьте тестовое сообщение на подключенный WhatsApp.

Для приема webhook локальная CRM должна иметь публичный HTTPS-адрес, направленный на `http://127.0.0.1:8000`.

### Личный MAX через GREEN-API

1. Создайте отдельный MAX-инстанс в GREEN-API и авторизуйте в нём личный аккаунт MAX.
2. Создайте аккаунт CRM типа `max` со статусом `active`.
3. Заполните `green_api_instance_id` и `green_api_token` значениями `idInstance` и `apiTokenInstance`.
4. Придумайте длинный `green_webhook_token`.
5. Оставьте выданные GREEN-API значения `green_api_url` и `green_media_url`.
6. Сохраните аккаунт.
7. В списке аккаунтов выберите его и выполните действие «Настроить webhook выбранных WhatsApp/MAX аккаунтов в GREEN-API».
8. Отправьте тестовое сообщение в подключённый личный аккаунт MAX и ответьте на него из CRM.

Для приёма webhook локальная CRM должна иметь публичный HTTPS-адрес, направленный на `http://127.0.0.1:8000`.
## 6. Повседневное управление

Посмотреть состояние:

```bash
docker compose ps
```

Посмотреть логи:

```bash
docker compose logs -f web connector worker
```

Перезапустить проект:

```bash
docker compose restart
```

Пересобрать после изменения кода:

```bash
docker compose up -d --build
```

Остановить без удаления данных:

```bash
docker compose down
```

## 7. Проверить проект

```bash
docker compose exec web python manage.py check
docker compose exec web python manage.py test
```

После изменения моделей проверьте миграции:

```bash
docker compose exec web python manage.py makemigrations --check --dry-run
```

## 8. Если запуск не удался

1. Выполните `docker compose ps`.
2. Выполните `docker compose logs --tail 200 web connector worker db`.
3. Сверьте пароли `DB_PASSWORD` и `MYSQL_ROOT_PASSWORD` в `.env`.
4. Проверьте, что порты 8000, 3307 и 6379 не заняты другим проектом.
5. Найдите нужный сценарий в [TECHNICAL_OPERATIONS.md](TECHNICAL_OPERATIONS.md#диагностика).

Не выполняйте `docker compose down -v`, если нужны локальные данные: параметр `-v` удаляет базу, медиа и сохраненные Docker-тома.