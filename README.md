# Omnichannel CRM System

Omnichannel CRM system for collecting messages from different messengers (Telegram) and responding from a single WEB interface.

## Tech Stack

- **Backend**: Python 3.12+, Django 5.1+, Django REST Framework
- **Database**: MySQL 8.0+ (optimized for high-load operations)
- **Telegram**: Telethon (personal accounts) + Bot API (bots)
- **Real-time**: Django Channels + WebSockets
- **Background Tasks**: Celery + Redis
- **Deployment**: Docker Compose

## Key Features

- ✅ Management of multiple personal Telegram accounts via Telethon
- ✅ Integration with existing bots via webhook
- ✅ Unified web interface for all chats
- ✅ Real-time updates via WebSockets
- ✅ Multi-tenant system (operators see only assigned chats)
- ✅ Automatic media file downloads
- ✅ Health monitoring system

## Quick Start

### Docker Compose (recommended)

```bash
# 1. Clone the repository
git clone https://github.com/printnewcode/omnichannel-crm.git
cd omnichannel-crm

# 2. Start all services
docker-compose up -d

# 3. Apply migrations
docker-compose exec web python manage.py migrate

# 4. Create superuser
docker-compose exec web python manage.py createsuperuser

# 5. Open admin panel
# http://localhost:8000/admin/
```

### Project Structure

```
omnichannel-crm/
├── CRM/                    # Main Django project
├── crm_app/               # Main application
│   ├── models.py          # Database models
│   ├── views.py           # REST API
│   ├── services/          # Business logic
│   └── ...
├── docker-compose.yml     # Docker configuration
└── requirements.txt       # Python dependencies
```

## Usage

### Adding Telegram Account

1. **Go to admin panel** `http://localhost:8000/admin/`
2. **Add account** in the "Telegram Accounts" section
3. **For personal account** fill in:
   - Name (descriptive name)
   - Account type: "Personal Account (Telethon)"
   - Phone number (+1234567890)
   - API ID and API Hash (from https://my.telegram.org/)
4. **Save and complete authentication** via API

### Adding Bot

1. **Get token** from @BotFather in Telegram
2. **Add account** in admin panel:
   - Name (bot name)
   - Account type: "Bot (pyTelegramBotAPI)"
   - Bot token (from @BotFather)
   - Bot username (without @)

### Bot Webhook Setup

For existing bot, set webhook to:
```
https://your-domain.com/api/webhook/bot/?token=YOUR_BOT_TOKEN
```

## Testing

```bash
# Run automated testing
docker-compose exec web python test_setup.py
```

## License

Apache-2.0