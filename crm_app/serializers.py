"""
Django REST Framework Serializers
"""
from rest_framework import serializers
from django.utils import timezone
from .models import (
    TelegramAccount, Chat, Message, Operator, ChatAssignment, HistoryImportJob,
    AISettings, QuickReply,
)


class TelegramAccountSerializer(serializers.ModelSerializer):
    """Serializer для TelegramAccount"""
    
    account_type_display = serializers.CharField(source='get_account_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = TelegramAccount
        fields = [
            'id', 'name', 'account_type', 'account_type_display', 'status', 'status_display',
            'phone_number', 'api_id', 'api_hash', 'session_string',
            'bot_token', 'bot_username', 'bridge_url', 'bridge_secret',
            'green_api_instance_id', 'green_api_token', 'green_webhook_token',
            'green_api_url', 'green_media_url',
            'telegram_user_id', 'first_name', 'last_name', 'username',
            'created_at', 'updated_at', 'last_activity',
            'last_error', 'error_count'
        ]
        read_only_fields = [
            'telegram_user_id', 'first_name', 'last_name', 'username',
            'created_at', 'updated_at', 'last_activity',
            'last_error', 'error_count'
        ]
        extra_kwargs = {
            'session_string': {'write_only': True},
            'bot_token': {'write_only': True},
            'bridge_secret': {'write_only': True},
            'green_api_token': {'write_only': True},
            'green_webhook_token': {'write_only': True},
            'api_hash': {'write_only': True}
        }


class ChatAccountSerializer(serializers.ModelSerializer):
    """Only the account fields required by the conversation list."""

    class Meta:
        model = TelegramAccount
        fields = ['id', 'name', 'account_type', 'status']
        read_only_fields = fields


class ChatSerializer(serializers.ModelSerializer):
    """Serializer для Chat"""
    
    chat_type_display = serializers.CharField(source='get_chat_type_display', read_only=True)
    telegram_account_name = serializers.CharField(source='telegram_account.name', read_only=True)
    telegram_account = ChatAccountSerializer(read_only=True)
    last_message_preview = serializers.SerializerMethodField()
    last_message_data = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    system_name = serializers.SerializerMethodField()
    google_contact_name = serializers.CharField(source='google_contact.display_name', read_only=True, allow_null=True)
    needs_human_attention = serializers.BooleanField(read_only=True)
    ai_active = serializers.SerializerMethodField()
    ai_status = serializers.SerializerMethodField()
    ai_status_reason = serializers.SerializerMethodField()
    
    class Meta:
        model = Chat
        fields = [
            'id', 'telegram_id', 'telegram_account', 'telegram_account_name',
            'chat_type', 'chat_type_display', 'title', 'username',
            'first_name', 'last_name',
            'message_count', 'unread_count', 'is_archived', 'is_bot',
            'created_at', 'updated_at', 'last_message_at',
            'last_message_preview', 'last_message_data'
            , 'display_name', 'system_name', 'google_contact_name',
            'needs_human_attention', 'ai_active', 'ai_status', 'ai_status_reason',
            'ai_paused_until', 'ai_disabled'
        ]
        read_only_fields = [
            'message_count', 'unread_count', 'is_archived', 'is_bot', 'created_at', 'updated_at', 'last_message_at'
        ]
    
    def get_last_message_preview(self, obj):
        """Получить превью последнего сообщения"""
        if hasattr(obj, 'latest_stored_message_preview'):
            if obj.latest_stored_message_preview:
                return obj.latest_stored_message_preview[:100]
            return '[Медиа]' if obj.last_message_at else None
        last_message = obj.messages.first()
        if last_message:
            text = last_message.text or last_message.media_caption or '[Медиа]'
            return text[:100]
        return None

    def get_last_message_data(self, obj):
        """Получить дату и другие данные последнего сообщения"""
        if hasattr(obj, 'latest_stored_message_preview'):
            if not obj.last_message_at:
                return None
            return {
                'telegram_date': obj.last_message_at.isoformat(),
            }
        last_message = obj.messages.first()
        if last_message:
            return {
                'telegram_date': last_message.telegram_date.isoformat(),
                'is_outgoing': last_message.is_outgoing
            }
        return None


    @staticmethod
    def _system_name(obj):
        return obj.title or obj.username or obj.first_name or f'Chat {obj.telegram_id}'

    def get_display_name(self, obj):
        return obj.google_contact.display_name if obj.google_contact_id else self._system_name(obj)

    def get_system_name(self, obj):
        return self._system_name(obj)

    def get_ai_active(self, obj):
        runtime = self.context.get('ai_runtime') or {}
        return self._ai_status(obj, runtime) == 'active'

    @staticmethod
    def _ai_status(obj, runtime):
        if obj.ai_disabled:
            return 'disabled'
        if obj.ai_paused_until and obj.ai_paused_until > timezone.now():
            return 'paused'
        if runtime.get('global_paused'):
            return 'global_paused'
        if not runtime.get('enabled'):
            return 'global_disabled'
        if runtime.get('operator_present') and not runtime.get('online_override_enabled'):
            return 'operator_paused'
        return 'active'

    def get_ai_status(self, obj):
        return self._ai_status(obj, self.context.get('ai_runtime') or {})

    def get_ai_status_reason(self, obj):
        runtime = self.context.get('ai_runtime') or {}
        ai_status = self._ai_status(obj, runtime)
        if ai_status == 'disabled':
            return 'ИИ отключён для этого диалога до ручного включения'
        if ai_status == 'paused':
            local_until = timezone.localtime(obj.ai_paused_until)
            return f'ИИ временно отключён до {local_until:%d.%m.%Y %H:%M}'
        if ai_status == 'global_disabled':
            return 'ИИ-автоответчик выключен в общих настройках'
        if ai_status == 'global_paused':
            paused_until = runtime.get('global_paused_until')
            if paused_until:
                local_until = timezone.localtime(paused_until)
                return f'ИИ-автоответчик временно выключен во всём проекте до {local_until:%d.%m.%Y %H:%M}'
            return 'ИИ-автоответчик временно выключен во всём проекте'
        if ai_status == 'operator_paused':
            return 'ИИ приостановлен: администратор работает в CRM'
        return 'Работает ИИ-автоответчик'


class MessageSerializer(serializers.ModelSerializer):
    """Serializer для Message"""
    
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    chat_title = serializers.CharField(source='chat.title', read_only=True)
    reply_to_preview = serializers.SerializerMethodField()
    media_file_name = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    can_react = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    special_content = serializers.SerializerMethodField()
    forward_info = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = [
            'id', 'telegram_id', 'external_message_id', 'chat', 'chat_title',
            'message_type', 'message_type_display', 'status', 'status_display',
            'text', 'is_outgoing',
            'from_user_id', 'from_user_name', 'from_user_username',
            'media_file_id', 'media_file_path', 'media_file_name', 'media_caption',
            'telegram_date', 'created_at', 'updated_at',
            'reply_to_message_id', 'reply_to_message', 'reply_to_preview',
            'metadata', 'special_content', 'forward_info', 'reactions', 'can_react'
        ]
        read_only_fields = [
            'telegram_date', 'created_at', 'updated_at'
        ]

    def to_representation(self, instance):
        """Repair legacy edit/delete webhook records without rewriting history."""
        data = super().to_representation(instance)
        metadata = instance.metadata if isinstance(instance.metadata, dict) else {}
        raw_type = metadata.get('raw_type')
        if raw_type in {'editedMessage', 'deletedMessage'}:
            from .services.message_content import normalize_green_message

            provider_content = metadata.get('provider_content')
            provider_content = provider_content if isinstance(provider_content, dict) else {}
            normalized = normalize_green_message({**provider_content, 'typeMessage': raw_type})
            data['message_type'] = normalized['message_type']
            data['message_type_display'] = dict(Message.MessageType.choices).get(
                normalized['message_type'], 'Сообщение'
            )
            data['text'] = normalized['text'] or data.get('text')
            data['special_content'] = normalized['special_content']
        return data
    
    def get_media_file_name(self, obj):
        """Return a user-facing original filename when it is known."""
        if hasattr(obj, 'api_original_filename'):
            original = obj.api_original_filename
        else:
            metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
            original = metadata.get('original_filename')
        if original:
            return original
        if obj.media_file_path:
            from pathlib import Path
            return Path(obj.media_file_path).name
        return None
    def get_reply_to_preview(self, obj):
        """Получить превью сообщения на которое отвечают"""
        if obj.reply_to_message:
            text = obj.reply_to_message.text or obj.reply_to_message.media_caption or '[Медиа]'
            return text[:100]
        return None

    def get_reactions(self, obj):
        if hasattr(obj, 'api_reactions'):
            return obj.api_reactions if isinstance(obj.api_reactions, list) else []
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        reactions = metadata.get('reactions')
        return reactions if isinstance(reactions, list) else []

    def get_special_content(self, obj):
        from .services.message_content import special_content_from_metadata
        return special_content_from_metadata(obj.message_type, obj.metadata)

    def get_forward_info(self, obj):
        from .services.message_content import forward_info_from_metadata
        return forward_info_from_metadata(obj.metadata)

    def get_metadata(self, obj):
        """Expose only UI state; provider payloads can contain large thumbnails."""
        source = obj.metadata if isinstance(obj.metadata, dict) else {}
        if hasattr(obj, 'api_provider_status'):
            values = {
                'provider_status': obj.api_provider_status,
                'delivery_id': str(obj.api_delivery_id) if obj.api_delivery_id not in (None, '') else None,
                'media_download': source.get('media_download'),
            }
        else:
            values = {
                'provider_status': source.get('provider_status'),
                'delivery_id': source.get('delivery_id'),
                'media_download': source.get('media_download'),
            }
        return {key: value for key, value in values.items() if value not in (None, '')}

    def get_can_react(self, obj):
        account = obj.chat.telegram_account
        if account.account_type == TelegramAccount.AccountType.PERSONAL:
            return bool(obj.telegram_id)
        return bool(
            account.account_type == TelegramAccount.AccountType.BOT
            and account.bot_token and not account.bridge_url and obj.telegram_id
        )


class OperatorSerializer(serializers.ModelSerializer):
    """Serializer для Operator"""
    
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = Operator
        fields = [
            'id', 'user', 'username', 'email',
            'is_active', 'max_chats', 'current_chats',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['current_chats', 'created_at', 'updated_at']


class ChatAssignmentSerializer(serializers.ModelSerializer):
    """Serializer для ChatAssignment"""
    
    chat_title = serializers.CharField(source='chat.title', read_only=True)
    operator_username = serializers.CharField(source='operator.user.username', read_only=True)
    
    class Meta:
        model = ChatAssignment
        fields = [
            'id', 'chat', 'chat_title', 'operator', 'operator_username',
            'assigned_at', 'unassigned_at', 'is_active'
        ]
        read_only_fields = ['assigned_at', 'unassigned_at']


class SendMessageSerializer(serializers.Serializer):
    """Validate a text message with up to ten attachments."""

    text = serializers.CharField(max_length=4096, required=False, allow_blank=True)
    idempotency_key = serializers.UUIDField(required=False)
    media_path = serializers.CharField(max_length=500, required=False, allow_null=True)
    media_paths = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        allow_empty=True,
        max_length=10,
    )

    def validate(self, data):
        paths = list(data.get('media_paths') or [])
        if data.get('media_path') and not paths:
            paths = [data['media_path']]
        data['media_paths'] = paths
        if not data.get('text') and not paths:
            raise serializers.ValidationError('Введите текст или прикрепите хотя бы один файл.')
        return data


class HistoryImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoryImportJob
        fields = [
            'id', 'kind', 'status', 'account_id', 'chat_id', 'parameters',
            'progress_current', 'progress_total', 'result', 'error',
            'created_at', 'started_at', 'finished_at',
        ]
        read_only_fields = fields


class AISettingsSerializer(serializers.ModelSerializer):
    operator_present = serializers.BooleanField(read_only=True)
    effective_enabled = serializers.SerializerMethodField()
    global_status = serializers.SerializerMethodField()

    class Meta:
        model = AISettings
        fields = [
            'enabled', 'paused_until', 'effective_enabled', 'global_status',
            'online_override_enabled', 'operator_present', 'model',
            'base_prompt', 'company_information', 'fallback_text',
            'offline_delay_seconds', 'online_delay_seconds', 'manual_pause_minutes',
            'presence_timeout_seconds', 'operator_idle_seconds',
            'max_incoming_age_minutes', 'context_message_limit',
            'context_character_limit', 'max_response_tokens', 'updated_at',
        ]
        read_only_fields = [
            'paused_until', 'effective_enabled', 'global_status', 'model',
            'online_override_enabled', 'operator_present', 'updated_at',
        ]

    def get_effective_enabled(self, obj):
        return obj.is_active()

    def get_global_status(self, obj):
        if not obj.enabled:
            return 'disabled'
        if obj.paused_until and obj.paused_until > timezone.now():
            return 'paused'
        return 'active'

    def validate_offline_delay_seconds(self, value):
        if not 5 <= value <= 3600:
            raise serializers.ValidationError('Допустимое значение: от 5 до 3600 секунд.')
        return value

    validate_online_delay_seconds = validate_offline_delay_seconds

    def validate_operator_idle_seconds(self, value):
        if not 30 <= value <= 3600:
            raise serializers.ValidationError('Допустимое значение: от 30 до 3600 секунд.')
        return value

    def validate_max_incoming_age_minutes(self, value):
        if not 1 <= value <= 1440:
            raise serializers.ValidationError('Допустимое значение: от 1 минуты до 24 часов.')
        return value

    def validate_manual_pause_minutes(self, value):
        if not 1 <= value <= 1440:
            raise serializers.ValidationError('Допустимое значение: от 1 до 1440 минут.')
        return value


class QuickReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = QuickReply
        fields = ['id', 'command', 'text', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_command(self, value):
        import re

        command = str(value or '').strip().lower()
        if command and not command.startswith('/'):
            command = f'/{command}'
        if not re.fullmatch(r'/[a-z0-9_]{1,30}', command):
            raise serializers.ValidationError(
                'Используйте от 1 до 30 латинских букв, цифр или символов подчёркивания после /.'
            )
        queryset = QuickReply.objects.filter(command=command)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('Такая команда уже существует.')
        return command

    def validate_text(self, value):
        text = str(value or '').strip()
        if not text:
            raise serializers.ValidationError('Введите текст быстрого ответа.')
        if len(text) > 4000:
            raise serializers.ValidationError('Текст быстрого ответа не может быть длиннее 4000 символов.')
        return text
