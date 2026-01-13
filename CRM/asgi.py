"""
ASGI config for CRM project.

Настройка для Django Channels WebSockets и HTTP
"""

import os
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')
django.setup()

# Импорт routing после настройки Django
from crm_app.routing import websocket_urlpatterns

# HTTP application (стандартный Django)
django_asgi_app = get_asgi_application()

# ASGI application с поддержкой WebSockets
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
