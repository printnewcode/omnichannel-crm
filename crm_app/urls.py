"""
URL конфигурация для crm_app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TelegramAccountViewSet, ChatViewSet, MessageViewSet,
    BotWebhookView, HealthCheckView, SystemStatusView, SystemControlView,
    SyncMessagesView, FileUploadView, HistoryImportJobViewSet
)
from .integration_views import JgetQuestionWebhookView, MaxWebhookView, WhatsAppWebhookView

router = DefaultRouter()
router.register(r'accounts', TelegramAccountViewSet, basename='telegram-account')
router.register(r'chats', ChatViewSet, basename='chat')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'history-imports', HistoryImportJobViewSet, basename='history-import')

urlpatterns = [
    path('api/', include(router.urls)),

    # Webhook endpoints
    path('api/webhook/bot/', BotWebhookView.as_view(), name='bot-webhook'),
    path('api/webhook/bot/<str:token>/', BotWebhookView.as_view(), name='bot-webhook-token'),
    path(
        'api/integrations/jget/<int:account_id>/questions/',
        JgetQuestionWebhookView.as_view(),
        name='jget-question-webhook',
    ),

    path('api/integrations/whatsapp/<int:account_id>/webhook/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),
    path('api/integrations/max/<int:account_id>/webhook/', MaxWebhookView.as_view(), name='max-webhook'),

    # Health and monitoring
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/system/status/', SystemStatusView.as_view(), name='system-status'),
    path('api/system/control/', SystemControlView.as_view(), name='system-control'),

    # File sync/polling
    path('api/sync/', SyncMessagesView.as_view(), name='message-sync'),

    # File upload
    path('api/upload/', FileUploadView.as_view(), name='file-upload'),
]
