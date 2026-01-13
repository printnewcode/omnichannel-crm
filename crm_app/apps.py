from django.apps import AppConfig


class CrmAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crm_app'
    
    def ready(self):
        """Инициализация при запуске приложения"""
        import crm_app.signals  # noqa