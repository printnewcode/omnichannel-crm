from django.apps import AppConfig
import os
import threading
import asyncio
import logging


class CrmAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crm_app'
    
    def ready(self):
        """Инициализация при запуске приложения"""
        import crm_app.signals  # noqa

        # Авто-запуск Telethon клиентов только в web процессе
        if os.environ.get("RUN_TELETHON_CLIENTS") != "1":
            return
        # Avoid double-start in Django autoreloader
        if os.environ.get("RUN_MAIN") == "false":
            return

        logger = logging.getLogger(__name__)

        def start_clients():
            from crm_app.services.telegram_client_manager import TelegramClientManager
            manager = TelegramClientManager()
            try:
                manager.start_all_active_sync()
                logger.info("Telethon clients auto-started via persistent loop")
            except Exception as e:
                logger.exception(f"Failed to auto-start Telethon clients: {e}")

        threading.Thread(target=start_clients, daemon=True).start()