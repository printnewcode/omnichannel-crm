# Деплой на Beget VPS

Инструкция рассчитана на Ubuntu, подключенный домен и размещение проекта в `/home/omnichannel-crm`.

Перед началом направьте A-запись домена на IP сервера.

## 1. Установить программы

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
sudo systemctl enable --now docker nginx
docker compose version
```

Последняя команда должна показать версию Docker Compose. Если вместо версии появилась ошибка, установите плагин отдельно:

```bash
apt install -y docker-compose-v2
docker compose version
```

Не переходите к запуску CRM, пока команда `docker compose version` не заработает.

## 2. Скачать проект

```bash
cd /home
sudo git clone <АДРЕС_РЕПОЗИТОРИЯ> omnichannel-crm
sudo chown -R $USER:$USER /home/omnichannel-crm
cd /home/omnichannel-crm
```

## 3. Настроить `.env`

```bash
cp deploy/.env.vps.example .env
nano .env
```

Замените в `.env`:

- `crm.example.com` на свой домен;
- `SECRET_KEY` на длинную случайную строку;
- `DB_PASSWORD` и `MYSQL_ROOT_PASSWORD` на разные сложные пароли.

Оставьте без изменений:

```env
DEBUG=False
LOCAL=False
RUN_TELETHON_CLIENTS=0
```

## 4. Запустить CRM

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml up -d --build
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml exec web python manage.py createsuperuser
```

Если сборка прервалась, сначала получите последние изменения и повторите её:

```bash
git pull
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml up -d --build
```

Проверить запуск:

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml ps
```

У сервисов должен быть статус `Up`, а у `db` и `redis` — `healthy`.

Compose-файл настроен для VPS с 1 CPU и 2 ГБ RAM. Контейнеры могут использовать не более 1,17 ГБ RAM суммарно, поэтому у системы и других проектов остаётся запас. MySQL и web получили дополнительный кратковременный запас CPU, но ресурсы не резервируются заранее и используются только при необходимости.

## Автоматическое восстановление

Docker уже поднимает контейнеры после их падения и после перезагрузки VPS. Чтобы автоматически восстанавливать также зависший web или `unhealthy` MySQL, один раз включите проверку:

```bash
cd /home/omnichannel-crm
sudo bash deploy/install-watchdog.sh
```

Проверка выполняется раз в минуту. Она не перезапускает работающие контейнеры: вмешательство происходит только при остановке сервиса, недоступности сайта или статусе MySQL `unhealthy`.

Проверить её состояние:

```bash
sudo systemctl status omnichannel-watchdog.timer --no-pager
```

Посмотреть последние автоматические действия:

```bash
sudo journalctl -t omnichannel-watchdog -n 30 --no-pager
```

## 5. Подключить домен и HTTPS

Откройте конфигурацию:

```bash
nano deploy/nginx-omnichannel-http.conf
```

Замените `crm.example.com` на свой домен. Если проект расположен не в `/home/omnichannel-crm`, замените также путь к нему.

```bash
sudo cp deploy/nginx-omnichannel-http.conf /etc/nginx/sites-available/omnichannel
sudo ln -s /etc/nginx/sites-available/omnichannel /etc/nginx/sites-enabled/omnichannel
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d ВАШ.ДОМЕН
```

После этого CRM должна открываться по адресу `https://ВАШ.ДОМЕН/`.

В админке создайте аккаунты мессенджеров. Для WhatsApp и MAX выполните действие настройки webhook. Tuna на сервере не требуется.

## Обновление

```bash
cd /home/omnichannel-crm
git pull
bash deploy/update.sh
sudo bash deploy/install-watchdog.sh
```

Миграции и статика применяются автоматически. Повторный запуск установки проверки безопасен и обновляет её файлы после `git pull`.

## Полезные команды

Статус:

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml ps
```

Логи:

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml logs --tail=100 web connector worker
```

Перезапуск:

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.vps.yml restart web connector worker
```

Проверка:

```bash
curl https://ВАШ.ДОМЕН/api/health/
```

Не удаляйте Docker volumes и каталог `sessions`: в них находятся база данных и авторизации Telegram.
