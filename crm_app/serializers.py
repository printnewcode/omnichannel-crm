"""
Django REST Framework Serializers
"""
from rest_framework import serializers
from .models import (
    TelegramAccount, Chat, Message, Operator, ChatAssignment
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
            'bot_token', 'bot_username', 'webhook_url',
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
            'api_hash': {'write_only': True}
        }


class ChatSerializer(serializers.ModelSerializer):
    """Serializer для Chat"""
    
    chat_type_display = serializers.CharField(source='get_chat_type_display', read_only=True)
    telegram_account_name = serializers.CharField(source='telegram_account.name', read_only=True)
    last_message_preview = serializers.SerializerMethodField()
    
    class Meta:
        model = Chat
        fields = [
            'id', 'telegram_id', 'telegram_account', 'telegram_account_name',
            'chat_type', 'chat_type_display', 'title', 'username',
            'first_name', 'last_name',
            'message_count', 'unread_count',
            'created_at', 'updated_at', 'last_message_at',
            'last_message_preview', 'metadata'
        ]
        read_only_fields = [
            'message_count', 'unread_count', 'created_at', 'updated_at', 'last_message_at'
        ]
    
    def get_last_message_preview(self, obj):
        """Получить превью последнего сообщения"""
        last_message = obj.messages.first()
        if last_message:
            text = last_message.text or last_message.media_caption or '[Медиа]'
            return text[:100]
        return None


class MessageSerializer(serializers.ModelSerializer):
    """Serializer для Message"""
    
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    chat_title = serializers.CharField(source='chat.title', read_only=True)
    reply_to_preview = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = [
            'id', 'telegram_id', 'chat', 'chat_title',
            'message_type', 'message_type_display', 'status', 'status_display',
            'text', 'is_outgoing',
            'from_user_id', 'from_user_name', 'from_user_username',
            'media_file_id', 'media_file_path', 'media_caption',
            'telegram_date', 'created_at', 'updated_at',
            'reply_to_message_id', 'reply_to_message', 'reply_to_preview',
            'metadata'
        ]
        read_only_fields = [
            'telegram_date', 'created_at', 'updated_at'
        ]
    
    def get_reply_to_preview(self, obj):
        """Получить превью сообщения на которое отвечают"""
        if obj.reply_to_message:
            text = obj.reply_to_message.text or obj.reply_to_message.media_caption or '[Медиа]'
            return text[:100]
        return None


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
    """Serializer для отправки сообщения"""
    
    text = serializers.CharField(max_length=4096, required=False, allow_blank=True)
    media_path = serializers.CharField(max_length=500, required=False, allow_null=True)
    
    def validate(self, data):
        """Валидация: должен быть текст или медиа"""
        if not data.get('text') and not data.get('media_path'):
            raise serializers.ValidationError("Either text or media_path is required")
        return data
