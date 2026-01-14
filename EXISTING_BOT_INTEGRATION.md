# 🔗 Integration Guide: Existing Bot with CRM

This guide shows how to integrate your existing Telegram bot with the Omnichannel CRM system.

## 📋 Integration Options

### Option 1: Webhook Forwarding (Recommended)
Forward messages from your existing bot to the CRM.

#### Step 1: Install Dependencies
Add to your existing bot project:
```bash
pip install aiohttp
```

#### Step 2: Add Forwarding Code
In your bot code, add this import and initialization:
```python
from crm_app.services.webhook_forwarder import WebhookForwarder

# Initialize forwarder
CRM_URL = "YOUR_CRM_DOMAIN_HERE"  # Your CRM domain
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

forwarder = WebhookForwarder(CRM_URL, BOT_TOKEN)
```

#### Step 3: Modify Message Handlers
Update your message handlers to forward messages:
```python
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Your existing logic
    print(f"Received: {message.text}")

    # Forward to CRM
    update_data = {
        'message': {
            'message_id': message.message_id,
            'from': {
                'id': message.from_user.id,
                'first_name': message.from_user.first_name,
                'last_name': message.from_user.last_name,
                'username': message.from_user.username,
            },
            'chat': {
                'id': message.chat.id,
                'type': message.chat.type,
                'title': getattr(message.chat, 'title', None),
                'username': getattr(message.chat, 'username', None),
                'first_name': getattr(message.chat, 'first_name', None),
                'last_name': getattr(message.chat, 'last_name', None),
            },
            'date': message.date,
            'text': message.text,
        }
    }

    # Forward asynchronously
    asyncio.create_task(forwarder.forward_webhook(update_data))
```

#### Step 4: Create Bot Account in CRM
1. Go to Django Admin → Telegram Accounts
2. Add new account:
   - **Name**: Your Bot Name
   - **Account Type**: Bot
   - **Bot Token**: YOUR_BOT_TOKEN_HERE
   - **Status**: Active

---

### Option 2: Polling Method
Let the CRM poll for messages instead of using webhooks.

#### Step 1: Add Bot to CRM Database
Same as Step 4 above - create the bot account in Django admin.

#### Step 2: Start Polling Service
```bash
# Start polling for all active bots
docker-compose exec web python manage.py start_bot_polling

# Or start polling for specific bot
docker-compose exec web python manage.py start_bot_polling --bot-tokens YOUR_BOT_TOKEN
```

#### Step 3: Stop Your Bot's Webhook
Since the CRM is polling, disable webhooks in your existing bot:
```python
# Remove webhook
bot.remove_webhook()

# Start polling instead
bot.polling()
```

---

### Option 3: Webhook Bridge Server
Run a separate bridge server that forwards webhooks.

#### Step 1: Configure Bridge
```python
from crm_app.services.webhook_forwarder import WebhookBridge

# Start bridge server on port 8081
bridge = WebhookBridge(listen_port=8081, crm_url="https://your-crm-domain.com")

# Add your bot
bridge.add_bot("YOUR_BOT_TOKEN")

# Start bridge
asyncio.run(bridge.start_bridge())
```

#### Step 2: Configure Your Bot Webhook
Point your bot's webhook to the bridge:
```python
# Set webhook to bridge server
bridge_url = "http://your-bridge-server:8081/webhook/bot/YOUR_BOT_TOKEN/"
bot.set_webhook(url=bridge_url)
```

---

## 🔧 Configuration

### Environment Variables
Add to your `.env` file:
```bash
# For webhook forwarding
CRM_BASE_URL=https://z2mpyh-178-206-114-247.ru.tuna.am

# For polling
POLLING_INTERVAL=1  # seconds between polls
POLLING_TIMEOUT=30  # long polling timeout
```

### Docker Service
Add polling service to `docker-compose.yml`:
```yaml
services:
  bot_polling:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: omnichannel_crm_bot_polling
    restart: unless-stopped
    command: python manage.py start_bot_polling
    volumes:
      - .:/app
      - logs_volume:/app/logs
    env_file:
      - .env
    depends_on:
      - db
      - redis
      - web
    networks:
      - crm_network
```

---

## 📊 Monitoring

### Check Active Bots
```bash
# View polling status
docker-compose logs bot_polling

# Check database
docker-compose exec web python manage.py shell -c "
from crm_app.models import TelegramAccount
bots = TelegramAccount.objects.filter(account_type='bot', status='active')
for bot in bots:
    print(f'{bot.name}: {bot.bot_token[:10]}...')
"
```

### Health Checks
- **Webhook forwarding**: Check CRM logs for successful forwards
- **Polling**: Monitor polling service logs for received messages

---

## 🆚 Comparison

| Method | Pros | Cons |
|--------|------|------|
| **Webhook Forwarding** | Real-time, low latency | Requires code changes in existing bot |
| **Polling** | No code changes needed | Higher latency, uses more API calls |
| **Bridge Server** | Clean separation | Additional infrastructure |

---

## 🐛 Troubleshooting

### Webhook Issues
```bash
# Check webhook endpoint
curl -X POST https://your-crm-domain.com/api/webhook/bot/YOUR_TOKEN/ \
  -H "Content-Type: application/json" \
  -d '{"test": "webhook"}'
```

### Polling Issues
```bash
# Test bot API
curl https://api.telegram.org/botYOUR_TOKEN/getMe

# Check polling logs
docker-compose logs bot_polling
```

### Common Errors
- **403 Forbidden**: Check bot token is correct
- **429 Too Many Requests**: Reduce polling frequency
- **Connection timeout**: Check network/firewall settings

---

## 🎯 Next Steps

1. **Choose integration method** based on your needs
2. **Test with sample messages**
3. **Monitor logs** for any issues
4. **Configure operators** in CRM to handle messages
5. **Set up automated responses** if needed

Your existing bot will now work seamlessly with the CRM! 🤖✨