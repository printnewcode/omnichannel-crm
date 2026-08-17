"""
Административный интерфейс Django
"""
from urllib.parse import urljoin, urlsplit

from django import forms
from django.conf import settings
from django.contrib import admin
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.contrib import messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import (
    TelegramAccount, Chat, Message, OutboundDelivery, HistoryImportJob
)


class TelegramAccountAdminForm(forms.ModelForm):
    """Human-friendly account form; provider credentials remain the only manual fields."""

    class Meta:
        model = TelegramAccount
        fields = '__all__'
        widgets = {
            'api_hash': forms.PasswordInput(render_value=True),
            'bot_token': forms.PasswordInput(render_value=True),
            'bridge_secret': forms.PasswordInput(render_value=True),
            'green_api_token': forms.PasswordInput(render_value=True),
            'green_webhook_token': forms.PasswordInput(render_value=True),
        }

    HELP_TEXTS = {
        'name': 'Понятное внутреннее название, например «Telegram поддержки» или «WhatsApp отдела продаж».',
        'account_type': 'Выберите мессенджер и способ подключения. После создания тип фиксируется.',
        'phone_number': 'Номер личного Telegram-аккаунта в международном формате, например +79991234567.',
        'api_id': mark_safe(
            'Числовой API ID приложения Telegram. Получить вместе с API Hash: '
            '<a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org/apps</a>.'
        ),
        'api_hash': mark_safe(
            'Секрет API Hash приложения Telegram. Получить: '
            '<a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org/apps</a>. '
            'Не передавайте его третьим лицам.'
        ),
        'bot_token': mark_safe(
            'Токен существующего Telegram-бота. Его выдаёт '
            '<a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>.'
        ),
        'bot_username': 'Username Telegram-бота без символа @, например support_bot.',
        'bridge_url': 'HTTPS-адрес обработчика в проекте бота, куда CRM отправляет ответ. Можно использовать {question_id}.',
        'bridge_secret': 'Общий секрет CRM и проекта Telegram-бота. Значение должно совпадать с BRIDGE_SECRET в конфигурации бота.',
        'green_api_instance_id': mark_safe(
            'idInstance нужного WhatsApp или MAX инстанса. Скопируйте в '
            '<a href="https://console.green-api.com/" target="_blank" rel="noopener">личном кабинете GREEN-API</a>.'
        ),
        'green_api_token': mark_safe(
            'apiTokenInstance этого же инстанса. Скопируйте в '
            '<a href="https://console.green-api.com/" target="_blank" rel="noopener">личном кабинете GREEN-API</a>.'
        ),
        'green_webhook_token': 'Придумайте отдельный длинный случайный секрет. GREEN-API будет передавать его CRM при каждом webhook-запросе.',
        'green_api_url': 'Базовый apiUrl инстанса. Обычно менять https://api.green-api.com не требуется; индивидуальный адрес указан в кабинете GREEN-API.',
        'green_media_url': 'Адрес загрузки файлов GREEN-API. Обычно менять https://media.green-api.com не требуется.',
    }

    LABELS = {
        'green_api_instance_id': 'GREEN-API idInstance',
        'green_api_token': 'GREEN-API apiTokenInstance',
        'green_webhook_token': 'Секрет webhook GREEN-API',
        'green_api_url': 'GREEN-API URL запросов',
        'green_media_url': 'GREEN-API URL файлов',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, help_text in self.HELP_TEXTS.items():
            if field_name in self.fields:
                self.fields[field_name].help_text = help_text
        for field_name, label in self.LABELS.items():
            if field_name in self.fields:
                self.fields[field_name].label = label


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    form = TelegramAccountAdminForm
    list_display = ['name', 'account_type', 'status', 'channel_connection', 'last_activity', 'error_summary', 'otp_link', 'qr_link']
    list_filter = ['account_type', 'status', 'created_at']
    ordering = ['account_type', 'name']
    list_per_page = 50
    save_on_top = True
    search_fields = ['name', 'phone_number', 'bot_username', 'username', 'green_api_instance_id']
    readonly_fields = [
        'status', 'session_string', 'telegram_user_id', 'first_name', 'last_name', 'username',
        'last_error', 'error_count', 'created_at', 'updated_at', 'last_activity',
    ]
    actions = ['start_authentication', 'resend_code', 'request_manual_code', 'start_accounts', 'stop_accounts', 'restart_accounts', 'check_auth_status', 'terminate_sessions', 'configure_green_api_webhooks']

    @admin.display(description='Подключение')
    def channel_connection(self, obj):
        if obj.account_type == TelegramAccount.AccountType.PERSONAL:
            return obj.phone_number or obj.username or 'Не указан телефон'
        if obj.account_type == TelegramAccount.AccountType.BOT:
            return f'@{obj.bot_username.lstrip(chr(64))}' if obj.bot_username else 'Не указан username бота'
        return f'idInstance {obj.green_api_instance_id}' if obj.green_api_instance_id else 'Не указан idInstance'

    @admin.display(description='Последняя ошибка')
    def error_summary(self, obj):
        if not obj.last_error:
            return '—'
        return obj.last_error[:90] + ('…' if len(obj.last_error) > 90 else '')

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'account_type', 'status'),
            'description': 'Укажите название и тип подключения. Статус меняется автоматически и через действия над аккаунтом.'
        }),
        ('Личный аккаунт (Telethon)', {
            'fields': ('phone_number', 'api_id', 'api_hash', 'session_string'),
            'classes': ('collapse',),
            'description': mark_safe(
                'Только для личного Telegram. API ID и API Hash создаются на '
                '<a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org/apps</a>. '
                'Сессия появится автоматически после авторизации.'
            ),
        }),
        ('Бот (pyTelegramBotAPI)', {
            'fields': ('bot_token', 'bot_username', 'bridge_url', 'bridge_secret'),
            'classes': ('collapse',),
            'description': mark_safe(
                'Только для существующего Telegram-бота. Bot Token выдаёт '
                '<a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a>; '
                'bridge-параметры должны совпадать с конфигурацией проекта бота.'
            ),
        }),
        ('GREEN-API (WhatsApp / личный MAX)', {
            'fields': ('green_api_instance_id', 'green_api_token', 'green_webhook_token', 'green_api_url', 'green_media_url'),
            'classes': ('collapse',),
            'description': mark_safe(
                'Только для WhatsApp и личного MAX. idInstance и apiTokenInstance находятся в '
                '<a href="https://console.green-api.com/" target="_blank" rel="noopener">личном кабинете GREEN-API</a>. '
                'После сохранения выберите действие «Настроить webhook».'
            ),
        }),
        ('Метаданные', {
            'fields': ('telegram_user_id', 'first_name', 'last_name', 'username'),
            'description': 'Заполняются автоматически после подключения аккаунта.'
        }),
        ('Ошибки', {
            'fields': ('last_error', 'error_count'),
            'classes': ('collapse',),
            'description': 'Служебная диагностика. Поля обновляются автоматически.'
        }),
        ('Временные метки', {
            'fields': ('created_at', 'updated_at', 'last_activity')
        }),
    )

    def get_fieldsets(self, request, obj=None):
        """Show only fields relevant to the selected messenger on change pages."""
        if obj is None:
            # Technical metadata and error history are populated after the first save.
            return self.fieldsets[:4]
        sections = [self.fieldsets[0]]
        if obj.account_type == TelegramAccount.AccountType.PERSONAL:
            sections.extend([self.fieldsets[1], self.fieldsets[4]])
        elif obj.account_type == TelegramAccount.AccountType.BOT:
            sections.append(self.fieldsets[2])
        elif obj.account_type in {TelegramAccount.AccountType.WHATSAPP, TelegramAccount.AccountType.MAX}:
            sections.append(self.fieldsets[3])
        sections.extend([self.fieldsets[5], self.fieldsets[6]])
        return tuple(sections)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append('account_type')
        return fields
    @admin.action(description='Настроить webhook выбранных WhatsApp/MAX аккаунтов в GREEN-API')
    def configure_green_api_webhooks(self, request, queryset):
        from .services.whatsapp_client import GreenAPIClient

        success = 0
        for account in queryset:
            if account.account_type not in {
                TelegramAccount.AccountType.WHATSAPP,
                TelegramAccount.AccountType.MAX,
            }:
                continue
            try:
                route_name = 'max-webhook' if account.account_type == TelegramAccount.AccountType.MAX else 'whatsapp-webhook'
                public_base_url = (settings.DOMAIN or '').strip().rstrip('/')
                if public_base_url and '://' not in public_base_url:
                    public_base_url = f'https://{public_base_url}'
                parsed = urlsplit(public_base_url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname in {'localhost', '127.0.0.1', '0.0.0.0'}:
                    raise ValueError(
                        'DOMAIN должен содержать публичный HTTPS-адрес CRM, например https://crm.example.com'
                    )
                webhook_url = urljoin(
                    f'{public_base_url}/',
                    reverse(route_name, kwargs={'account_id': account.id}).lstrip('/'),
                )
                GreenAPIClient(account).configure_webhook(webhook_url)
                success += 1
            except Exception as exc:
                self.message_user(request, f'{account.name}: {exc}', level=messages.ERROR)
        if success:
            self.message_user(request, f'GREEN-API webhooks настроены: {success}', level=messages.SUCCESS)
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
        """Request startup in the dedicated connector process."""
        ready_ids = []
        for account in queryset:
            if account.account_type != TelegramAccount.AccountType.PERSONAL:
                self.message_user(request, f'«{account.name}»: действие доступно только для личного Telegram.', level=messages.WARNING)
            elif not account.session_string:
                self.message_user(request, f'«{account.name}»: сначала выполните авторизацию.', level=messages.WARNING)
            else:
                ready_ids.append(account.id)

        updated = TelegramAccount.objects.filter(id__in=ready_ids).update(
            status=TelegramAccount.AccountStatus.ACTIVE,
            last_error='',
            restart_requested_at=None,
        )
        if updated:
            self.message_user(request, f'Запуск передан connector: {updated}. Подключение займёт несколько секунд.')

    start_accounts.short_description = "▶️ Запустить аккаунты через connector"
    def stop_accounts(self, request, queryset):
        """Request shutdown in the dedicated connector process."""
        updated = queryset.filter(
            account_type=TelegramAccount.AccountType.PERSONAL,
        ).update(
            status=TelegramAccount.AccountStatus.INACTIVE,
            restart_requested_at=None,
        )
        if updated:
            self.message_user(request, f'Остановка передана connector: {updated}.')
        else:
            self.message_user(request, 'Личные Telegram-аккаунты не выбраны.', level=messages.WARNING)

    stop_accounts.short_description = "⏹️ Остановить аккаунты через connector"
    def restart_accounts(self, request, queryset):
        """Request reconnect in the dedicated connector process."""
        from django.utils import timezone

        ready_ids = [
            account.id for account in queryset
            if account.account_type == TelegramAccount.AccountType.PERSONAL and account.session_string
        ]
        updated = TelegramAccount.objects.filter(id__in=ready_ids).update(
            status=TelegramAccount.AccountStatus.ACTIVE,
            last_error='',
            restart_requested_at=timezone.now(),
        )
        if updated:
            self.message_user(request, f'Перезапуск передан connector: {updated}.')
        else:
            self.message_user(request, 'Нет авторизованных личных Telegram-аккаунтов.', level=messages.WARNING)

    restart_accounts.short_description = "🔄 Перезапустить аккаунты через connector"
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

    def running_status(self, obj):
        """Show desired connector state; Telethon runs in another process."""
        if obj.account_type != TelegramAccount.AccountType.PERSONAL:
            return '-'
        if obj.status == TelegramAccount.AccountStatus.ACTIVE:
            return "🟢 Включен"
        if obj.status == TelegramAccount.AccountStatus.ERROR:
            return "⚠️ Ошибка"
        if obj.status == TelegramAccount.AccountStatus.AUTHENTICATING:
            return "🔐 Авторизация"
        return "⏹️ Отключен"
    running_status.short_description = "Режим connector"
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
    list_display = ['title', 'chat_type', 'is_bot', 'telegram_account', 'is_archived', 'unread_count', 'last_message_at']
    list_filter = ['is_archived', 'is_bot', 'chat_type', 'created_at', 'telegram_account']
    search_fields = ['title', 'username', 'first_name', 'telegram_id']
    readonly_fields = ['created_at', 'updated_at', 'last_message_at']
    raw_id_fields = ['telegram_account']

    def has_add_permission(self, request):
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    @admin.display(description='ID у провайдера')
    def provider_message_id(self, obj):
        return obj.external_message_id or obj.telegram_id or '—'
    list_display = ['provider_message_id', 'chat', 'message_type', 'status', 'is_outgoing', 'telegram_date']
    list_filter = ['message_type', 'status', 'is_outgoing', 'telegram_date']
    search_fields = ['text', 'telegram_id', 'external_message_id', 'from_user_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['chat', 'reply_to_message']
    date_hierarchy = 'telegram_date'

    def has_add_permission(self, request):
        return False


@admin.register(OutboundDelivery)
class OutboundDeliveryAdmin(admin.ModelAdmin):
    list_display = ['id', 'chat', 'status', 'attempts', 'provider_message_id', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['text', 'last_error', 'provider_message_id']
    readonly_fields = ['idempotency_key', 'attempts', 'provider_message_id', 'created_message', 'created_at', 'updated_at']
    raw_id_fields = ['chat', 'reply_to_message', 'requested_by']

    def has_add_permission(self, request):
        return False


@admin.register(HistoryImportJob)
class HistoryImportJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'kind', 'status', 'account', 'chat', 'progress_current', 'created_at', 'finished_at']
    list_filter = ['kind', 'status', 'created_at']
    search_fields = ['account__name', 'chat__title', 'error']
    readonly_fields = [
        'kind', 'status', 'account', 'chat', 'requested_by', 'parameters',
        'progress_current', 'progress_total', 'result', 'error',
        'created_at', 'started_at', 'finished_at',
    ]

    def has_add_permission(self, request):
        return False

admin.site.site_header = 'Omnichannel CRM — администрирование'
admin.site.site_title = 'Omnichannel CRM'
admin.site.index_title = 'Каналы, диалоги и сообщения'
