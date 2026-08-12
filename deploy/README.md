# Деплой на Linux VPS

Практическая инструкция для Ubuntu 22.04/24.04. Команды выполняются от пользователя с `sudo`. В примерах проект размещен в `/opt/omnichannel-crm`, а домен — `crm.example.com`.

## 1. Подготовить сервер

Укажите A-запись домена на IP VPS, затем установите Docker, Nginx и Certbot:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git nginx certbot python3-certbot-nginx
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker nginx
```

Откройте только SSH, HTTP и HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 2. Скопировать проект и заполнить окружение

```bash
sudo mkdir -p /opt/omnichannel-crm
sudo chown "$USER":"$USER" /opt/omnichannel-crm
git clone <URL_РЕПОЗИТОРИЯ> /opt/omnichannel-crm
cd /opt/omnichannel-crm
cp deploy/.env.vps.example .env
mkdir -p staticfiles media sessions logs backups
chmod 700 sessions backups
nano .env
```

В `.env` обязательно замените домен, `SECRET_KEY`, `DB_PASSWORD` и `MYSQL_ROOT_PASSWORD`. Секреты можно создать так:

```bash
openssl rand -hex 48
```

Оставьте `DEBUG=False`, `LOCAL=False` и `RUN_TELETHON_CLIENTS=0`.

## 3. Первый запуск

```bash
cd /opt/omnichannel-crm
docker compose -f deploy/docker-compose.vps.yml config --quiet
docker compose -f deploy/docker-compose.vps.yml up -d --build
docker compose -f deploy/docker-compose.vps.yml ps
docker compose -f deploy/docker-compose.vps.yml exec web python manage.py createsuperuser
```

Все сервисы запускаются автоматически после перезагрузки VPS. Telethon работает только в контейнере `connector`; не запускайте второй экземпляр connector для тех же Telegram-сессий.

## 4. Подключить домен и HTTPS

Сначала установите временный HTTP-конфиг, заменив домен и путь:

```bash
cd /opt/omnichannel-crm
sed -e 's/crm.example.com/ВАШ.ДОМЕН/g' -e 's#/opt/omnichannel-crm#/opt/omnichannel-crm#g' deploy/nginx-omnichannel-http.conf | sudo tee /etc/nginx/sites-available/omnichannel >/dev/null
sudo ln -sfn /etc/nginx/sites-available/omnichannel /etc/nginx/sites-enabled/omnichannel
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d ВАШ.ДОМЕН
```

Затем установите HTTPS-конфиг:

```bash
sed -e 's/crm.example.com/ВАШ.ДОМЕН/g' -e 's#/opt/omnichannel-crm#/opt/omnichannel-crm#g' deploy/nginx-omnichannel.conf | sudo tee /etc/nginx/sites-available/omnichannel >/dev/null
sudo nginx -t
sudo systemctl reload nginx
curl -fsS https://ВАШ.ДОМЕН/api/health/
```

В админке добавьте аккаунты. Для WhatsApp и MAX настройте публичные webhook URL через действия аккаунтов. Tuna на production не нужен.

## 5. Обновление

Перед обновлением сделайте резервную копию, затем:

```bash
cd /opt/omnichannel-crm
git pull --ff-only
docker compose -f deploy/docker-compose.vps.yml up -d --build --remove-orphans
docker compose -f deploy/docker-compose.vps.yml exec web python manage.py check --deploy
docker compose -f deploy/docker-compose.vps.yml ps
docker compose -f deploy/docker-compose.vps.yml logs --since=10m web connector worker
```

Миграции и сборка статики выполняются контейнером `migrate` до запуска новой версии web.

## 6. Ежедневное управление

```bash
cd /opt/omnichannel-crm
docker compose -f deploy/docker-compose.vps.yml ps
docker compose -f deploy/docker-compose.vps.yml logs -f --tail=200 web connector worker
docker compose -f deploy/docker-compose.vps.yml restart web connector worker
docker stats
```

Проверка приложения:

```bash
curl -fsS https://ВАШ.ДОМЕН/api/health/
docker compose -f deploy/docker-compose.vps.yml exec web python manage.py check
docker compose -f deploy/docker-compose.vps.yml exec web python manage.py showmigrations --plan
```

## 7. Резервные копии

Создать копию базы и пользовательских файлов:

```bash
cd /opt/omnichannel-crm
STAMP=$(date +%F_%H-%M)
docker compose -f deploy/docker-compose.vps.yml exec -T db sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction "$MYSQL_DATABASE"' | gzip > "backups/db_$STAMP.sql.gz"
tar -czf "backups/files_$STAMP.tar.gz" media sessions .env
find backups -type f -mtime +14 -delete
```

Скопируйте каталог `backups` за пределы VPS. Telegram-сессии находятся в `sessions`, входящие файлы — в `media`.

Восстановление базы выполняйте только на остановленном приложении и после отдельной резервной копии:

```bash
docker compose -f deploy/docker-compose.vps.yml stop web connector worker
gunzip -c backups/db_ДАТА.sql.gz | docker compose -f deploy/docker-compose.vps.yml exec -T db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
docker compose -f deploy/docker-compose.vps.yml start web connector worker
```

## 8. Если что-то не запустилось

```bash
docker compose -f deploy/docker-compose.vps.yml ps -a
docker compose -f deploy/docker-compose.vps.yml logs --tail=300 migrate web connector worker db redis
sudo nginx -t
sudo journalctl -u nginx --since '30 minutes ago'
df -h
free -h
```

Не удаляйте Docker volumes и каталог `sessions`: в них находятся база и авторизации Telegram.