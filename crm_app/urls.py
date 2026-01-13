"""
URL конфигурация для crm_app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TelegramAccountViewSet, ChatViewSet, MessageViewSet,
    BotWebhookView, HealthCheckView, SystemStatusView, SystemControlView,
    FileUploadView
)

router = DefaultRouter()
router.register(r'accounts', TelegramAccountViewSet, basename='telegram-account')
router.register(r'chats', ChatViewSet, basename='chat')
router.register(r'messages', MessageViewSet, basename='message')

urlpatterns = [
    path('api/', include(router.urls)),

    # Webhook endpoints
    path('api/webhook/bot/', BotWebhookView.as_view(), name='bot-webhook'),
    path('api/webhook/bot/<str:token>/', BotWebhookView.as_view(), name='bot-webhook-token'),

    # Health and monitoring
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/system/status/', SystemStatusView.as_view(), name='system-status'),
    path('api/system/control/', SystemControlView.as_view(), name='system-control'),

    # File upload
    path('api/upload/', FileUploadView.as_view(), name='file-upload'),
]
