"""
Административный интерфейс Django
"""
from django.contrib import admin
from django.shortcuts import render, redirect
from django.urls import path
from django.contrib import messages
from django.utils.html import format_html
from .models import (
    TelegramAccount, Chat, Message, Operator, ChatAssignment
)


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = ['name', 'account_type', 'status', 'phone_number', 'bot_username', 'last_activity', 'otp_link']
    list_filter = ['account_type', 'status', 'created_at']
    search_fields = ['name', 'phone_number', 'bot_username', 'username']
    readonly_fields = ['created_at', 'updated_at', 'last_activity']
    actions = ['start_authentication', 'resend_code', 'start_accounts', 'stop_accounts', 'restart_accounts']

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'account_type', 'status')
        }),
        ('Личный аккаунт (Hydrogram)', {
            'fields': ('phone_number', 'api_id', 'api_hash', 'session_string'),
            'classes': ('collapse',)
        }),
        ('Бот (pyTelegramBotAPI)', {
            'fields': ('bot_token', 'bot_username'),
            'classes': ('collapse',)
        }),
        ('Метаданные', {
            'fields': ('telegram_user_id', 'first_name', 'last_name', 'username')
        }),
        ('Ошибки', {
            'fields': ('last_error', 'error_count'),
            'classes': ('collapse',)
        }),
        ('Временные метки', {
            'fields': ('created_at', 'updated_at', 'last_activity')
        }),
    )

    def start_authentication(self, request, queryset):
        """Запустить аутентификацию для выбранных личных аккаунтов"""
        from .services.telegram_client_manager import TelegramClientManager
        from asgiref.sync import async_to_sync
        import asyncio

        success_count = 0
        error_count = 0

        for account in queryset:
            if account.account_type != TelegramAccount.AccountType.PERSONAL:
                self.message_user(
                    request,
                    f'Аккаунт "{account.name}" не является личным аккаунтом',
                    level='warning'
                )
                continue

            try:
                # Use sync wrapper method
                manager = TelegramClientManager()
                result = manager.authenticate_account_sync(account)

                if result['success']:
                    success_count += 1
                    self.message_user(
                        request,
                        f'Аутентификация для "{account.name}" начата. Проверьте Telegram для OTP.'
                    )
                else:
                    error_count += 1
                    self.message_user(
                        request,
                        f'Ошибка аутентификации для "{account.name}": {result.get("error", "Неизвестная ошибка")}',
                        level='error'
                    )
            except Exception as e:
                error_count += 1
                self.message_user(
                    request,
                    f'Ошибка при запуске аутентификации для "{account.name}": {str(e)}',
                    level='error'
                )

        if success_count > 0:
            self.message_user(request, f'Аутентификация начата для {success_count} аккаунтов.')
        if error_count > 0:
            self.message_user(request, f'Ошибки в {error_count} аккаунтах.', level='warning')

    start_authentication.short_description = "🚀 Начать аутентификацию (личные аккаунты)"

    def resend_code(self, request, queryset):
        """Resend OTP code using a different verification method"""
        success_count = 0
        error_count = 0

        for account in queryset:
            if account.account_type != TelegramAccount.AccountType.PERSONAL:
                self.message_user(
                    request,
                    f'Аккаунт "{account.name}" не является личным аккаунтом',
                    level='warning'
                )
                continue

            try:
                manager = TelegramClientManager()
                result = manager.resend_code_sync(account)

                if result['success']:
                    success_count += 1
                    code_type = result.get('code_type', 'unknown')
                    self.message_user(
                        request,
                        f'Код для "{account.name}" отправлен повторно через {code_type}: {result.get("message", "")}'
                    )
                else:
                    error_count += 1
                    self.message_user(
                        request,
                        f'Ошибка при повторной отправке кода для "{account.name}": {result.get("error", "Неизвестная ошибка")}',
                        level='error'
                    )
            except Exception as e:
                error_count += 1
                self.message_user(
                    request,
                    f'Ошибка при повторной отправке кода для "{account.name}": {str(e)}',
                    level='error'
                )

        if success_count > 0:
            self.message_user(request, f'Код отправлен повторно для {success_count} аккаунтов.')
        if error_count > 0:
            self.message_user(request, f'Ошибки в {error_count} аккаунтах.', level='warning')

    resend_code.short_description = "🔄 Отправить код повторно (другой метод)"

    def start_accounts(self, request, queryset):
        """Запустить выбранные аккаунты"""
        from .services.telegram_client_manager import TelegramClientManager
        from asgiref.sync import async_to_sync

        success_count = 0

        for account in queryset:
            try:
                manager = TelegramClientManager()
                result = manager.start_client_sync(account)

                if result:
                    success_count += 1
                    self.message_user(request, f'Аккаунт "{account.name}" запущен.')
                else:
                    self.message_user(
                        request,
                        f'Не удалось запустить "{account.name}": {account.last_error}',
                        level='error'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f'Ошибка при запуске "{account.name}": {str(e)}',
                    level='error'
                )

        self.message_user(request, f'Запущено {success_count} из {queryset.count()} аккаунтов.')

    start_accounts.short_description = "▶️ Запустить аккаунты"

    def stop_accounts(self, request, queryset):
        """Остановить выбранные аккаунты"""
        from .services.telegram_client_manager import TelegramClientManager
        from asgiref.sync import async_to_sync

        stopped_count = 0

        for account in queryset:
            try:
                manager = TelegramClientManager()
                result = manager.stop_client_sync(account.id)

                if result:
                    stopped_count += 1
                    self.message_user(request, f'Аккаунт "{account.name}" остановлен.')
                else:
                    self.message_user(
                        request,
                        f'Не удалось остановить "{account.name}"',
                        level='warning'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f'Ошибка при остановке "{account.name}": {str(e)}',
                    level='error'
                )

        self.message_user(request, f'Остановлено {stopped_count} из {queryset.count()} аккаунтов.')

    stop_accounts.short_description = "⏹️ Остановить аккаунты"

    def restart_accounts(self, request, queryset):
        """Перезапустить выбранные аккаунты"""
        from .services.telegram_client_manager import TelegramClientManager
        from asgiref.sync import async_to_sync

        restarted_count = 0

        for account in queryset:
            try:
                manager = TelegramClientManager()
                result = manager.restart_client_sync(account.id)

                if result:
                    restarted_count += 1
                    self.message_user(request, f'Аккаунт "{account.name}" перезапущен.')
                else:
                    self.message_user(
                        request,
                        f'Не удалось перезапустить "{account.name}": {account.last_error}',
                        level='error'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f'Ошибка при перезапуске "{account.name}": {str(e)}',
                    level='error'
                )

        self.message_user(request, f'Перезапущено {restarted_count} из {queryset.count()} аккаунтов.')

    restart_accounts.short_description = "🔄 Перезапустить аккаунты"

    def otp_link(self, obj):
        """Ссылка на верификацию OTP"""
        if obj.account_type == TelegramAccount.AccountType.PERSONAL and obj.status == TelegramAccount.AccountStatus.AUTHENTICATING:
            url = f'/admin/crm_app/telegramaccount/{obj.id}/verify_otp/'
            return format_html('<a href="{}" class="button" style="background: #ff6b35; color: white; padding: 3px 8px; border-radius: 3px;">Verify OTP</a>', url)
        return ''
    otp_link.short_description = 'OTP'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:account_id>/verify_otp/', self.verify_otp_view, name='verify_otp'),
        ]
        return custom_urls + urls

    def verify_otp_view(self, request, account_id):
        """View для верификации OTP кода"""
        try:
            account = TelegramAccount.objects.get(id=account_id)
        except TelegramAccount.DoesNotExist:
            messages.error(request, "Аккаунт не найден.")
            return redirect('admin:crm_app_telegramaccount_changelist')

        if request.method == 'POST':
            otp_code = request.POST.get('otp_code')
            password = request.POST.get('password')  # Для 2FA

            if not otp_code:
                messages.error(request, "Введите OTP код.")
                return redirect(request.path)

            from .services.telegram_client_manager import TelegramClientManager
            from asgiref.sync import async_to_sync

            try:
                manager = TelegramClientManager()
                result = manager.verify_otp_sync(account, otp_code, password)

                if result['success']:
                    messages.success(request, f'Аккаунт "{account.name}" успешно аутентифицирован!')
                    return redirect('admin:crm_app_telegramaccount_change', account.id)
                else:
                    error_msg = result.get("error", "Неизвестная ошибка")
                    # Check if this is an automatic restart message
                    if "Автоматически запущена новая аутентификация" in error_msg:
                        messages.info(request, f'Для аккаунта "{account.name}": {error_msg}')
                        return redirect('admin:crm_app_telegramaccount_change', account.id)
                    else:
                        messages.error(request, f'Ошибка верификации: {error_msg}')

            except Exception as e:
                messages.error(request, f'Ошибка при верификации: {str(e)}')
        else:
            # GET request - send a fresh code for verification
            if account.status == 'authenticating':
                from .services.telegram_client_manager import TelegramClientManager

                try:
                    manager = TelegramClientManager()
                    result = manager.send_verification_code_sync(account)

                    if result['success']:
                        messages.info(request, f'Отправлен новый код для верификации через {result.get("code_type", "SMS")}')
                    else:
                        messages.warning(request, f'Не удалось отправить код: {result.get("error", "Неизвестная ошибка")}')

                except Exception as e:
                    messages.warning(request, f'Не удалось отправить код автоматически: {str(e)}')
                    messages.info(request, 'Введите код из Telegram вручную')

        context = {
            'account': account,
            'opts': self.model._meta,
            'has_change_permission': self.has_change_permission(request, account),
        }
        return render(request, 'admin/telegram_account_verify_otp.html', context)


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ['title', 'chat_type', 'telegram_account', 'unread_count', 'last_message_at']
    list_filter = ['chat_type', 'created_at', 'telegram_account']
    search_fields = ['title', 'username', 'first_name', 'telegram_id']
    readonly_fields = ['created_at', 'updated_at', 'last_message_at']
    raw_id_fields = ['telegram_account']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['telegram_id', 'chat', 'message_type', 'status', 'is_outgoing', 'telegram_date']
    list_filter = ['message_type', 'status', 'is_outgoing', 'telegram_date']
    search_fields = ['text', 'telegram_id', 'from_user_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['chat', 'reply_to_message']
    date_hierarchy = 'telegram_date'


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_active', 'max_chats', 'current_chats']
    list_filter = ['is_active', 'created_at']
    search_fields = ['user__username', 'user__email']
    raw_id_fields = ['user']


@admin.register(ChatAssignment)
class ChatAssignmentAdmin(admin.ModelAdmin):
    list_display = ['chat', 'operator', 'is_active', 'assigned_at']
    list_filter = ['is_active', 'assigned_at']
    raw_id_fields = ['chat', 'operator']
