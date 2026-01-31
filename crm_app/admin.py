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
    list_display = ['name', 'account_type', 'status', 'running_status', 'phone_number', 'bot_username', 'last_activity', 'otp_link', 'qr_link']
    list_filter = ['account_type', 'status', 'created_at']
    search_fields = ['name', 'phone_number', 'bot_username', 'username']
    readonly_fields = ['created_at', 'updated_at', 'last_activity']
    actions = ['start_authentication', 'resend_code', 'request_manual_code', 'start_accounts', 'stop_accounts', 'restart_accounts', 'check_auth_status', 'terminate_sessions']

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'account_type', 'status')
        }),
        ('Личный аккаунт (Telethon)', {
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

            # Validate required fields before attempting authentication
            if not account.phone_number:
                self.message_user(
                    request,
                    f'Аккаунт "{account.name}": Номер телефона не указан',
                    level='error'
                )
                error_count += 1
                continue

            if not account.api_id or not account.api_hash:
                self.message_user(
                    request,
                    f'Аккаунт "{account.name}": API ID и API Hash обязательны',
                    level='error'
                )
                error_count += 1
                continue

            try:
                # Use sync wrapper method
                manager = TelegramClientManager()
                result = manager.authenticate_account_sync(account)

                if result['success']:
                    success_count += 1
                    code_type = result.get('code_type', 'SMS')
                    next_type = result.get('next_type', '')
                    message = result.get('message', '')

                    success_msg = f'Аутентификация для "{account.name}" начата через {code_type}'
                    if next_type:
                        success_msg += f' (следующий метод: {next_type})'
                    if message:
                        success_msg += f'. {message}'

                    self.message_user(request, success_msg)

                    # Additional guidance for Russian users
                    if '+7' in account.phone_number:
                        self.message_user(
                            request,
                            f'💡 Для российских номеров: если SMS не приходит, попробуйте "Отправить код повторно" через несколько минут',
                            level='info'
                        )
                else:
                    error_count += 1
                    error_msg = result.get("error", "Неизвестная ошибка")
                    self.message_user(
                        request,
                        f'Ошибка аутентификации для "{account.name}": {error_msg}',
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
            self.message_user(request, f'✅ Аутентификация начата для {success_count} аккаунтов.')
        if error_count > 0:
            self.message_user(request, f'❌ Ошибки в {error_count} аккаунтах.', level='warning')

    start_authentication.short_description = "🚀 Начать аутентификацию (личные аккаунты)"

    def resend_code(self, request, queryset):
        """Resend OTP code using a different verification method"""
        from .services.telegram_client_manager import TelegramClientManager

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

    def request_manual_code(self, request, queryset):
        """Request OTP code manually for debugging"""
        from .services.telegram_client_manager import TelegramClientManager

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
                result = manager.send_verification_code_sync(account)

                if result['success']:
                    success_count += 1
                    code_type = result.get('code_type', 'unknown')
                    self.message_user(
                        request,
                        f'Код для "{account.name}" отправлен вручную через {code_type}: {result.get("message", "")}'
                    )
                else:
                    error_count += 1
                    self.message_user(
                        request,
                        f'Ошибка при ручной отправке кода для "{account.name}": {result.get("error", "Неизвестная ошибка")}',
                        level='error'
                    )
            except Exception as e:
                error_count += 1
                self.message_user(
                    request,
                    f'Ошибка при ручной отправке кода для "{account.name}": {str(e)}',
                    level='error'
                )

        if success_count > 0:
            self.message_user(request, f'Код отправлен вручную для {success_count} аккаунтов.')
        if error_count > 0:
            self.message_user(request, f'Ошибки в {error_count} аккаунтах.', level='warning')

    request_manual_code.short_description = "📱 Запросить код вручную (для отладки)"

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

    def terminate_sessions(self, request, queryset):
        """Force logout and clear all session data from Telegram and DB"""
        from .services.telegram_client_manager import TelegramClientManager
        success_count = 0
        
        manager = TelegramClientManager()
        for account in queryset:
            if account.account_type != TelegramAccount.AccountType.PERSONAL:
                continue
            
            result = manager.terminate_session_sync(account)
            if result.get('success'):
                success_count += 1
                self.message_user(request, f'💥 Сессия для "{account.name}" полностью аннулирована и удалена.')
            else:
                self.message_user(request, f'⚠️ Ошибка при удалении сессии "{account.name}": {result.get("error")}', level='error')
        
        self.message_user(request, f'Удалено {success_count} сессий.')

    terminate_sessions.short_description = "💥 Аннулировать сессии (Полный выход)"

    def check_auth_status(self, request, queryset):
        """Check if Telegram session is still valid"""
        from .services.telegram_client_manager import TelegramClientManager
        success_count = 0
        error_count = 0
        
        manager = TelegramClientManager()
        for account in queryset:
            if account.account_type != TelegramAccount.AccountType.PERSONAL:
                continue
                
            result = manager.check_authorization_sync(account)
            if result.get('success'):
                if result.get('authorized'):
                    success_count += 1
                    self.message_user(request, f'✅ Аккаунт "{account.name}" авторизован.')
                else:
                    error_count += 1
                    self.message_user(request, f'❌ Аккаунт "{account.name}" НЕ авторизован (сессия отозвана).', level='error')
            else:
                error_count += 1
                self.message_user(request, f'⚠️ Ошибка проверки "{account.name}": {result.get("error")}', level='warning')
        
        self.message_user(request, f'Проверено {queryset.count()} аккаунтов. Активных: {success_count}.')

    check_auth_status.short_description = "🔍 Проверить статус авторизации"

    def changelist_view(self, request, extra_context=None):
        """Perform automatic session check for active accounts (once every 15 min)"""
        from django.utils import timezone
        from .services.telegram_client_manager import TelegramClientManager
        import threading

        # Only check on the first page or when not filtering to avoid excessive load
        if not request.GET or 'p' not in request.GET:
            manager = TelegramClientManager()
            # Find accounts that are ACTIVE but haven't been checked in 15 minutes
            check_threshold = timezone.now() - timezone.timedelta(minutes=15)
            # We don't have a 'last_checked_at' field in the model, so we use 'updated_at' as a proxy 
            # or just do it for all ACTIVE ones in a separate thread to avoid blocking UI
            accounts_to_check = TelegramAccount.objects.filter(
                account_type=TelegramAccount.AccountType.PERSONAL,
                status=TelegramAccount.AccountStatus.ACTIVE
            )
            
            # Start background check if there are any accounts
            if accounts_to_check.exists():
                def background_check():
                    for account in accounts_to_check:
                        manager.check_authorization_sync(account)
                
                threading.Thread(target=background_check, daemon=True).start()

        return super().changelist_view(request, extra_context=extra_context)

    def running_status(self, obj):
        """Показывает запущен ли клиент"""
        if obj.account_type != TelegramAccount.AccountType.PERSONAL:
            return '-'
        from .services.telegram_client_manager import TelegramClientManager
        manager = TelegramClientManager()
        return "✅ Запущен" if obj.id in manager.get_running_accounts() else "⏹️ Остановлен"
    running_status.short_description = "Состояние клиента"

    def otp_link(self, obj):
        """Ссылка на верификацию OTP"""
        if obj.account_type == TelegramAccount.AccountType.PERSONAL:
            # Always show button for personal accounts
            url = f'/admin/crm_app/telegramaccount/{obj.id}/verify_otp/'
            return format_html('<a href="{}" class="button" style="background: #ff6b35; color: white; padding: 3px 8px; border-radius: 3px;">Verify OTP</a>', url)
        return ''
    otp_link.short_description = 'OTP'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:account_id>/verify_otp/', self.verify_otp_view, name='verify_otp'),
            path('<int:account_id>/qr_login/', self.qr_login_view, name='qr_login'),
        ]
        return custom_urls + urls

    def qr_link(self, obj):
        """Ссылка на QR login"""
        if obj.account_type == TelegramAccount.AccountType.PERSONAL:
            # Always show button for personal accounts
            url = f'/admin/crm_app/telegramaccount/{obj.id}/qr_login/'
            return format_html('<a href="{}" class="button" style="background: #2d8cf0; color: white; padding: 3px 8px; border-radius: 3px;">QR Login</a>', url)
        return ''
    qr_link.short_description = 'QR'

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
                    messages.success(request, f'Аккаунт "{account.name}" успешно аутентифицирован! Не забудьте активировать (запустить) аккаунт.')
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
            # GET request - do NOT send a new code automatically.
            # Opening the Verify OTP page should not invalidate the previously sent code.
            if account.status == 'authenticating':
                messages.info(
                    request,
                    'Введите код, который уже был отправлен. '
                    'Если кода нет — используйте "Отправить код повторно" или '
                    '"Запросить код вручную".'
                )

        context = {
            'account': account,
            'opts': self.model._meta,
            'has_change_permission': self.has_change_permission(request, account),
        }
        return render(request, 'admin/telegram_account_verify_otp.html', context)

    def qr_login_view(self, request, account_id):
        """View для QR login"""
        try:
            account = TelegramAccount.objects.get(id=account_id)
        except TelegramAccount.DoesNotExist:
            messages.error(request, "Аккаунт не найден.")
            return redirect('admin:crm_app_telegramaccount_changelist')

        if account.account_type != TelegramAccount.AccountType.PERSONAL:
            messages.error(request, "QR login доступен только для личных аккаунтов.")
            return redirect('admin:crm_app_telegramaccount_change', account.id)

        from .services.telegram_client_manager import TelegramClientManager
        import qrcode
        import base64
        from io import BytesIO

        manager = TelegramClientManager()
        qr_url = None
        status_message = None
        is_authenticated = False

        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'check':
                password = request.POST.get('password') or None
                result = manager.check_qr_login_sync(account, password=password)
                if result.get('success') and result.get('status') == 'authenticated':
                    messages.success(request, f'Аккаунт "{account.name}" успешно аутентифицирован через QR! Не забудьте активировать (запустить) аккаунт.')
                    return redirect('admin:crm_app_telegramaccount_change', account.id)
                elif result.get('success') and result.get('status') == 'pending':
                    status_message = 'Ожидаем сканирование QR кода...'
                    qr_url = result.get('qr_url')
                elif result.get('status') == 'password_required':
                    status_message = 'Требуется пароль 2FA. Введите пароль и нажмите "Проверить".'
                    qr_url = result.get('qr_url')
                else:
                    messages.error(request, result.get('error', 'Не удалось проверить QR'))
            else:
                result = manager.create_qr_login_sync(account)
                if result.get('success'):
                    qr_url = result.get('qr_url')
                else:
                    messages.error(request, result.get('error', 'Не удалось создать QR'))
        else:
            result = manager.create_qr_login_sync(account)
            if result.get('success'):
                qr_url = result.get('qr_url')
            else:
                messages.error(request, result.get('error', 'Не удалось создать QR'))

        qr_image_b64 = None
        if qr_url:
            img = qrcode.make(qr_url)
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            qr_image_b64 = base64.b64encode(buffer.getvalue()).decode('ascii')
        else:
            if status_message is None:
                status_message = 'QR код генерируется. Нажмите "Обновить QR" через пару секунд.'

        context = {
            'account': account,
            'opts': self.model._meta,
            'has_change_permission': self.has_change_permission(request, account),
            'qr_image_b64': qr_image_b64,
            'qr_url': qr_url,
            'status_message': status_message,
        }
        return render(request, 'admin/telegram_account_qr_login.html', context)


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
