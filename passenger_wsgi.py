import os
import sys

# Change directory to the app root
path = os.path.dirname(os.path.abspath(__file__))
if path not in sys.path:
    sys.path.append(path)

# Set environment variables
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CRM.settings')
os.environ['RUN_TELETHON_CLIENTS'] = '0'  # Don't auto-start in WSGI process

from django.core.wsgi import get_wsgi_application

# Beget uses 'application' as the default WSGI callable
application = get_wsgi_application()
