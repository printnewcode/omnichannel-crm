# Omnichannel CRM System

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

## Getting Started (Local Setup)

### 1. Prerequisites
- Python 3.12+
- MySQL (optional, SQLite is used by default for local development)

### 2. Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd omnichannel-crm
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### 3. Configuration
1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and set `LOCAL=True` for SQLite or configure MySQL credentials. Also set `SECRET_KEY`.

### 4. Database & Admin
1. Run migrations:
   ```bash
   python manage.py migrate
   ```
2. Create a superuser to access the admin panel:
   ```bash
   python manage.py createsuperuser
   ```

### 5. Running the Application
1. Start the development server:
   ```bash
   python manage.py runserver
   ```
2. In a separate terminal, run the Telegram sync command:
   ```bash
   python manage.py sync_telegram
   ```

Access the admin panel at `http://127.0.0.1:8000/admin/`.

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
In a process.

## License
Apache-2.0