"""
URL конфигурация для crm_app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TelegramAccountViewSet, ChatViewSet, MessageViewSet,
    BotWebhookView, HealthCheckView, SystemStatusView, SystemControlView,
    SyncMessagesView, FileUploadView, HistoryImportJobViewSet
    , AISettingsView, AIPresenceView, AIOnlineOverrideView, AIGlobalModeView,
    GoogleContactsStatusView, GoogleContactsConnectView,
    GoogleContactsCallbackView, GoogleContactsSyncView,
    QuickReplyViewSet,
)
from .integration_views import JgetQuestionWebhookView, MaxWebhookView, WhatsAppWebhookView

router = DefaultRouter()
router.register(r'accounts', TelegramAccountViewSet, basename='telegram-account')
router.register(r'chats', ChatViewSet, basename='chat')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'history-imports', HistoryImportJobViewSet, basename='history-import')
router.register(r'quick-replies', QuickReplyViewSet, basename='quick-reply')

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
    path('api/ai/settings/', AISettingsView.as_view(), name='ai-settings'),
    path('api/ai/presence/', AIPresenceView.as_view(), name='ai-presence'),
    path('api/ai/online-override/', AIOnlineOverrideView.as_view(), name='ai-online-override'),
    path('api/ai/global-mode/', AIGlobalModeView.as_view(), name='ai-global-mode'),
    path('api/google-contacts/status/', GoogleContactsStatusView.as_view(), name='google-contacts-status'),
    path('api/google-contacts/connect/', GoogleContactsConnectView.as_view(), name='google-contacts-connect'),
    path('api/google-contacts/callback/', GoogleContactsCallbackView.as_view(), name='google-contacts-callback'),
    path('api/google-contacts/sync/', GoogleContactsSyncView.as_view(), name='google-contacts-sync'),
]
