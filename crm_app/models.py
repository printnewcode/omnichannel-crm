"""
Оптимизированные модели для MySQL с поддержкой высоконагруженных записей сообщений
"""
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator
from django.utils import timezone
import json


class TelegramAccount(models.Model):
    """Модель для хранения данных Telegram аккаунтов (личные аккаунты и боты)"""
    
    class AccountType(models.TextChoices):
        PERSONAL = 'personal', 'Личный аккаунт (Hydrogram)'
        BOT = 'bot', 'Бот (pyTelegramBotAPI)'
    
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
    
    # Для личных аккаунтов (Hydrogram)
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
        help_text="StringSession для Hydrogram"
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
    webhook_url = models.URLField(
        null=True,
        blank=True,
        verbose_name="URL для webhook"
    )
    
    # Метаданные
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
    
    # Ошибки и логи
    last_error = models.TextField(null=True, blank=True)
    error_count = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = "Telegram Аккаунт"
        verbose_name_plural = "Telegram Аккаунты"
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
        unique=True,
        db_index=True,
        verbose_name="Telegram Chat ID"
    )
    telegram_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.CASCADE,
        related_name='chats',
        db_index=True,
        verbose_name="Telegram Аккаунт"
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
    
    # Дополнительные данные (JSON)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"
        indexes = [
            models.Index(fields=['telegram_account', 'last_message_at']),
            models.Index(fields=['chat_type', 'last_message_at']),
            models.Index(fields=['created_at']),
            # Составной индекс для частых запросов
            models.Index(fields=['telegram_account', 'unread_count', 'last_message_at']),
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
        db_index=True,
        verbose_name="Telegram Message ID"
    )
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
    
    # Временные метки
    telegram_date = models.DateTimeField(db_index=True, verbose_name="Дата в Telegram")
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