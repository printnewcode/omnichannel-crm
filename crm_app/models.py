"""
Оптимизированные модели для MySQL с поддержкой высоконагруженных записей сообщений
"""
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator
from django.utils import timezone
import json
import uuid


class TelegramAccount(models.Model):
    """Модель для хранения данных Telegram аккаунтов (личные аккаунты и боты)"""
    
    class AccountType(models.TextChoices):
        PERSONAL = 'personal', 'Telegram — личный аккаунт'
        BOT = 'bot', 'Telegram — бот'
        WHATSAPP = 'whatsapp', 'WhatsApp через GREEN-API'
        MAX = 'max', 'MAX — личный аккаунт через GREEN-API'
    
    class AccountStatus(models.TextChoices):
        ACTIVE = 'active', 'Активен'
        INACTIVE = 'inactive', 'Неактивен'
        AUTHENTICATING = 'authenticating', 'Авторизация'
        ERROR = 'error', 'Ошибка'
    
    # Основные поля
    name = models.CharField(max_length=255, verbose_name="Название аккаунта")
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        verbose_name="Тип аккаунта"
    )
    status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.INACTIVE,
        verbose_name="Статус"
    )
    
    # Для личных аккаунтов (Telethon)
    phone_number = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        unique=True,
        verbose_name="Номер телефона"
    )
    api_id = models.BigIntegerField(null=True, blank=True, verbose_name="API ID")
    api_hash = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="API Hash"
    )
    session_string = models.TextField(
        null=True,
        blank=True,
        help_text="StringSession для Telethon"
    )
    pending_session_string = models.TextField(
        null=True,
        blank=True,
        help_text="Временная StringSession для этапа отправки OTP"
    )
    pending_session_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Имя временной сессии для OTP (файловая сессия)"
    )
    pending_phone_code_hash = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Хэш кода подтверждения (OTP)"
    )
    pending_code_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Время отправки OTP"
    )
    pending_code_type = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text="Тип OTP (SMS/APP/CALL/FLASH_CALL)"
    )
    
    # Для ботов
    bot_token = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        verbose_name="Bot Token"
    )
    bot_username = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="Username бота"
    )
    bridge_url = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="URL JGET bridge для отправки ответа; поддерживает {question_id}",
    )
    bridge_secret = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Общий секрет для HMAC-подписи запросов между ботом и CRM",
    )
    
    # Метаданные
    # Provider API credentials for webhook-based integrations.
    access_token = models.TextField(null=True, blank=True)
    webhook_secret = models.CharField(max_length=255, null=True, blank=True)
    webhook_verify_token = models.CharField(max_length=255, null=True, blank=True)
    app_secret = models.CharField(max_length=255, null=True, blank=True)
    phone_number_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    business_account_id = models.CharField(max_length=100, null=True, blank=True)
    api_version = models.CharField(max_length=32, default='v23.0', blank=True)
    green_api_instance_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    green_api_token = models.TextField(null=True, blank=True)
    green_webhook_token = models.CharField(max_length=255, null=True, blank=True)
    green_api_url = models.URLField(max_length=500, default='https://api.green-api.com', blank=True)
    green_media_url = models.URLField(max_length=500, default='https://media.green-api.com', blank=True)
    telegram_user_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Telegram User ID"
    )
    first_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    username = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    
    # Временные метки
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity = models.DateTimeField(null=True, blank=True)
    restart_requested_at = models.DateTimeField(null=True, blank=True, editable=False)
    
    # Ошибки и логи
    last_error = models.TextField(null=True, blank=True)
    error_count = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = "Аккаунт мессенджера"
        verbose_name_plural = "Аккаунты мессенджеров"
        indexes = [
            models.Index(fields=['status', 'account_type']),
            models.Index(fields=['telegram_user_id']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"


class Chat(models.Model):
    """Модель для чатов/диалогов"""
    
    class ChatType(models.TextChoices):
        PRIVATE = 'private', 'Личный чат'
        GROUP = 'group', 'Группа'
        SUPERGROUP = 'supergroup', 'Супергруппа'
        CHANNEL = 'channel', 'Канал'
    
    # Идентификаторы
    telegram_id = models.BigIntegerField(
        db_index=True,
        verbose_name="ID чата у провайдера"
    )
    telegram_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.CASCADE,
        related_name='chats',
        db_index=True,
        verbose_name="Аккаунт мессенджера"
    )
    
    # Информация о чате
    chat_type = models.CharField(
        max_length=20,
        choices=ChatType.choices,
        verbose_name="Тип чата"
    )
    title = models.CharField(max_length=255, null=True, blank=True)
    username = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    
    # Статистика
    message_count = models.IntegerField(default=0)
    unread_count = models.IntegerField(default=0)
    
    # Временные метки
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="В архиве")
    is_bot = models.BooleanField(default=False, db_index=True, verbose_name="Собеседник — бот")
    
    # Дополнительные данные (JSON)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"
        indexes = [
            models.Index(fields=['telegram_account', 'last_message_at']),
            models.Index(fields=['chat_type', 'last_message_at']),
            models.Index(fields=['chat_type', 'is_archived', 'last_message_at'], name='crm_chat_type_arch_idx'),
            models.Index(fields=['created_at']),
            # Составной индекс для частых запросов
            models.Index(fields=['telegram_account', 'unread_count', 'last_message_at']),
            models.Index(fields=['telegram_account', 'is_archived', 'last_message_at'], name='crm_chat_account_arch_idx'),
        ]
        unique_together = [['telegram_id', 'telegram_account']]
    
    def __str__(self):
        name = self.title or self.first_name or self.username or f"Chat {self.telegram_id}"
        return f"{name} ({self.telegram_account.name})"


class Message(models.Model):
    """Оптимизированная модель для сообщений с индексацией для MySQL"""
    
    class MessageType(models.TextChoices):
        TEXT = 'text', 'Текст'
        PHOTO = 'photo', 'Фото'
        VIDEO = 'video', 'Видео'
        VOICE = 'voice', 'Голосовое'
        AUDIO = 'audio', 'Аудио'
        DOCUMENT = 'document', 'Документ'
        STICKER = 'sticker', 'Стикер'
        LOCATION = 'location', 'Локация'
        CONTACT = 'contact', 'Контакт'
        OTHER = 'other', 'Другое'
    
    class MessageStatus(models.TextChoices):
        RECEIVED = 'received', 'Получено'
        SENT = 'sent', 'Отправлено'
        PENDING = 'pending', 'Ожидает отправки'
        FAILED = 'failed', 'Ошибка отправки'
    
    # Идентификаторы
    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="ID сообщения у провайдера"
    )
    external_message_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='messages',
        db_index=True,
        verbose_name="Чат"
    )
    
    # Информация о сообщении
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
        verbose_name="Тип сообщения"
    )
    status = models.CharField(
        max_length=20,
        choices=MessageStatus.choices,
        default=MessageStatus.RECEIVED,
        verbose_name="Статус"
    )
    text = models.TextField(null=True, blank=True)
    
    # Отправитель/Получатель
    from_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    from_user_name = models.CharField(max_length=255, null=True, blank=True)
    from_user_username = models.CharField(max_length=255, null=True, blank=True)
    is_outgoing = models.BooleanField(default=False, db_index=True, verbose_name="Исходящее")
    
    # Медиа
    media_file_id = models.CharField(max_length=255, null=True, blank=True)
    media_file_path = models.CharField(max_length=500, null=True, blank=True)
    media_caption = models.TextField(null=True, blank=True)
    telegram_file_id = models.CharField(max_length=255, null=True, blank=True, help_text="Telegram file ID для повторного скачивания")
    
    # Временные метки
    telegram_date = models.DateTimeField(db_index=True, verbose_name="Дата сообщения")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Для ответов
    reply_to_message = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies'
    )
    
    # Дополнительные данные (JSON)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        indexes = [
            models.Index(fields=['chat', 'telegram_date']),
            models.Index(fields=['chat', 'is_outgoing', 'telegram_date']),
            models.Index(fields=['from_user_id', 'telegram_date']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['message_type']),
            # Уникальность сообщения в рамках чата
        ]
        unique_together = [['telegram_id', 'chat']]
        constraints = [
            models.UniqueConstraint(
                fields=['chat', 'external_message_id'],
                name='unique_external_message_per_chat',
            ),
        ]
        ordering = ['-telegram_date']
    
    def __str__(self):
        text_preview = (self.text or self.media_caption or 'Медиа')[:50]
        return f"Message {self.telegram_id}: {text_preview}"


class Operator(models.Model):
    """Расширенная модель оператора (связь с User)"""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='operator_profile',
        verbose_name="Пользователь"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    max_chats = models.IntegerField(default=50, verbose_name="Максимум чатов")
    current_chats = models.IntegerField(default=0, verbose_name="Текущее количество чатов")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Оператор"
        verbose_name_plural = "Операторы"
    
    def __str__(self):
        return f"Оператор: {self.user.username}"


class ChatAssignment(models.Model):
    """Назначение чатов операторам (Multi-tenant)"""
    chat = models.OneToOneField(
        Chat,
        on_delete=models.CASCADE,
        related_name='assignment',
        verbose_name="Чат"
    )
    operator = models.ForeignKey(
        Operator,
        on_delete=models.CASCADE,
        related_name='assigned_chats',
        db_index=True,
        verbose_name="Оператор"
    )
    assigned_at = models.DateTimeField(auto_now_add=True, db_index=True)
    unassigned_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    
    class Meta:
        verbose_name = "Назначение чата"
        verbose_name_plural = "Назначения чатов"
        indexes = [
            models.Index(fields=['operator', 'is_active', 'assigned_at']),
        ]
    
    def __str__(self):
        return f"{self.chat} -> {self.operator.user.username}"

class OutboundDelivery(models.Model):
    """Durable outbox item consumed by the connector process."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает отправки'
        PROCESSING = 'processing', 'Отправляется'
        RETRY = 'retry', 'Повторная попытка'
        SENT = 'sent', 'Отправлено'
        FAILED = 'failed', 'Ошибка'

    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='outbound_deliveries')
    reply_to_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='outbound_delivery_replies',
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_message_deliveries',
    )
    text = models.TextField(blank=True)
    media_path = models.CharField(max_length=500, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True)
    created_message = models.OneToOneField(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='outbound_delivery',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Исходящая отправка'
        verbose_name_plural = 'Исходящие отправки'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['status', 'available_at'], name='crm_app_out_status_5a4c40_idx'),
            models.Index(fields=['chat', 'created_at'], name='crm_app_out_chat_id_233f61_idx'),
        ]

    def __str__(self):
        return f'OutboundDelivery {self.pk} ({self.status})'


class HistoryImportJob(models.Model):
    """Progress record for provider history imports executed by Celery."""

    class Kind(models.TextChoices):
        CHAT_HISTORY = 'chat_history', 'История диалога'
        CHAT_DISCOVERY = 'chat_discovery', 'Загрузка диалогов'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает'
        RUNNING = 'running', 'Выполняется'
        COMPLETED = 'completed', 'Завершено'
        FAILED = 'failed', 'Ошибка'

    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    account = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE, related_name='history_import_jobs')
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='history_import_jobs', null=True, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]
