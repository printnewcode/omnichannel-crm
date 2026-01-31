# Omnichannel CRM System (Shared Hosting Edition)

Omnichannel CRM system for collecting messages from Telegram and responding from a single web interface. Adapted for deployment on Beget Shared Hosting.

## Tech Stack

- **Backend**: Python 3.12+, Django 5.1+, Django REST Framework
- **Database**: MySQL 8.0+
- **Telegram**: Telethon (personal accounts) + Bot API (bots)
- **Real-time**: AJAX Polling (optimized for Shared Hosting)
- **Background Tasks**: Cron (replaces Celery/Redis)
- **Deployment**: Passenger WSGI

## Key Features

- ✅ Multiple Telegram accounts (Personal & Bot)
- ✅ AJAX Polling for "real-time" updates (no WebSockets needed)
- ✅ Cron-based Telegram synchronization
- ✅ Integration with existing bots via webhook
- ✅ Media file downloads

## Deployment on Beget Shared Hosting

### 1. File Preparation
Upload all files to your Beget site root via SFTP.

### 2. Python Configuration
In Beget CP -> **Web sites** -> **Python Settings**:
1. Select **Passenger**.
2. Point the "Path to application" to the project root.
3. Ensure `passenger_wsgi.py` exists in the root.

### 3. Database
1. Create a MySQL database in Beget CP.
2. Edit `.env` with your database credentials.
3. Run migrations via SSH:
   ```bash
   python3 manage.py migrate
   python3 manage.py createsuperuser
   ```

### 4. Cron Jobs Setup
Add these to **Cron** in Beget CP:

| Schedule | Command |
| :--- | :--- |
| `* * * * *` | `python3 ~/site.com/public_html/manage.py sync_telegram` |
| `0 0 * * *` | `python3 ~/site.com/public_html/manage.py cleanup_messages` |

## Usage

### Adding Telegram Account
1. Go to `/admin/` -> **Telegram Accounts**.
2. For personal accounts, enter API ID, API Hash, and Phone.
3. Use the management command or UI to start authentication.

### Existing Bot Integration
Set your existing bot's webhook to:
`https://your-domain.com/api/webhook/bot/`

## License
Apache-2.0