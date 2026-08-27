from datetime import timedelta
from unittest.mock import Mock, patch
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm_app.models import (
    AISettings,
    Chat,
    ChatAIState,
    GoogleContact,
    GoogleContactsIntegration,
    Message,
    OperatorPresenceSession,
    OutboundDelivery,
    TelegramAccount,
)
from crm_app.serializers import ChatSerializer
from crm_app.services.ai_assistant import (
    process_ai_reply,
    operator_is_present,
    register_incoming_message,
    register_manual_outgoing,
    register_provider_outgoing,
    request_ai_decision,
)
from crm_app.services.google_contacts import chat_phone, match_chat_contact, normalize_phone, sync_contacts
from crm_app.tasks import process_incoming_message


class AIAssistantTests(TestCase):
    def setUp(self):
        self.account = TelegramAccount.objects.create(
            name='Telegram',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )
        self.chat = Chat.objects.create(
            telegram_id=100,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
            title='Client',
        )
        self.config = AISettings.load()
        self.config.enabled = True
        self.config.company_information = 'Мы работаем ежедневно с 10:00 до 20:00.'
        self.config.save()

    def message(self, *, outgoing=False, message_type=Message.MessageType.TEXT, text='Когда вы работаете?', telegram_date=None):
        return Message.objects.create(
            chat=self.chat,
            message_type=message_type,
            status=Message.MessageStatus.SENT if outgoing else Message.MessageStatus.RECEIVED,
            text=text,
            is_outgoing=outgoing,
            telegram_date=telegram_date or timezone.now(),
        )

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_incoming_message_creates_one_pending_generation(self, apply_async):
        with self.captureOnCommitCallbacks(execute=True):
            first = self.message(text='Здравствуйте')
            register_incoming_message(first.id)
            second = self.message(text='Когда вы работаете?')
            register_incoming_message(second.id)

        state = ChatAIState.objects.get(chat=self.chat)
        self.assertEqual(state.source_message, second)
        self.assertEqual(state.generation, 2)
        self.assertEqual(apply_async.call_count, 2)

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_stale_provider_message_is_not_scheduled(self, apply_async):
        stale = self.message(telegram_date=timezone.now() - timedelta(minutes=11))

        register_incoming_message(stale.id)

        self.assertFalse(ChatAIState.objects.exists())
        apply_async.assert_not_called()

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_chat_specific_disable_and_pause_do_not_schedule_messages(self, apply_async):
        self.chat.ai_disabled = True
        self.chat.save(update_fields=['ai_disabled'])
        register_incoming_message(self.message(text='Сообщение при постоянном отключении').id)

        self.chat.ai_disabled = False
        self.chat.ai_paused_until = timezone.now() + timedelta(hours=2)
        self.chat.save(update_fields=['ai_disabled', 'ai_paused_until'])
        register_incoming_message(self.message(text='Сообщение во время паузы').id)

        self.assertFalse(ChatAIState.objects.exists())
        apply_async.assert_not_called()

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_private_chats_from_every_supported_messenger_are_scheduled(self, apply_async):
        supported = [
            TelegramAccount.AccountType.PERSONAL,
            TelegramAccount.AccountType.BOT,
            TelegramAccount.AccountType.WHATSAPP,
            TelegramAccount.AccountType.MAX,
        ]
        for account_type in supported:
            with self.subTest(account_type=account_type):
                ChatAIState.objects.filter(chat=self.chat).delete()
                self.account.account_type = account_type
                self.account.save(update_fields=['account_type'])
                with self.captureOnCommitCallbacks(execute=True):
                    incoming = self.message(text=f'Вопрос из {account_type}')
                    register_incoming_message(incoming.id)
                self.assertEqual(ChatAIState.objects.get(chat=self.chat).source_message, incoming)
        self.assertEqual(apply_async.call_count, len(supported))

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_groups_and_bots_are_never_scheduled(self, apply_async):
        self.chat.chat_type = Chat.ChatType.GROUP
        self.chat.save(update_fields=['chat_type'])
        register_incoming_message(self.message().id)
        self.assertFalse(ChatAIState.objects.exists())
        apply_async.assert_not_called()

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_private_customer_chat_from_telegram_bot_is_scheduled(self, apply_async):
        self.account.account_type = TelegramAccount.AccountType.BOT
        self.account.save(update_fields=['account_type'])
        incoming = self.message()

        with self.captureOnCommitCallbacks(execute=True):
            register_incoming_message(incoming.id)

        self.assertEqual(ChatAIState.objects.get(chat=self.chat).source_message, incoming)
        apply_async.assert_called_once()

    @patch('crm_app.tasks.publish_message')
    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_telegram_bot_webhook_task_schedules_new_customer_message(self, apply_async, _publish):
        self.account.account_type = TelegramAccount.AccountType.BOT
        self.account.save(update_fields=['account_type'])

        with self.captureOnCommitCallbacks(execute=True):
            message_id = process_incoming_message.run(
                account_id=self.account.id,
                chat_id=self.chat.id,
                telegram_message_id=9001,
                telegram_date=timezone.now().isoformat(),
                text='Подскажите график работы',
                is_outgoing=False,
            )

        self.assertEqual(ChatAIState.objects.get(chat=self.chat).source_message_id, message_id)
        apply_async.assert_called_once()

    def test_last_outgoing_message_prevents_ai_reply(self):
        incoming = self.message()
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)
        self.message(outgoing=True, text='Администратор уже ответил')

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'already-answered')
        self.assertFalse(OutboundDelivery.objects.exists())

    def test_pending_message_expires_without_late_reply(self):
        incoming = self.message(telegram_date=timezone.now() - timedelta(minutes=11))
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'expired-message')
        self.assertFalse(OutboundDelivery.objects.exists())
        self.assertIsNone(ChatAIState.objects.get(chat=self.chat).source_message_id)

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_manual_reply_cancels_pending_and_pauses_chat(self, apply_async):
        incoming = self.message()
        register_incoming_message(incoming.id)
        outgoing = self.message(outgoing=True, text='Ответ администратора')
        register_manual_outgoing(outgoing.id)

        self.chat.refresh_from_db()
        state = ChatAIState.objects.get(chat=self.chat)
        self.assertIsNone(state.source_message_id)
        self.assertGreater(self.chat.ai_paused_until, timezone.now() + timedelta(minutes=59))

    def test_unrelated_phone_message_is_not_mistaken_for_processing_ai_reply(self):
        OutboundDelivery.objects.create(
            chat=self.chat,
            origin=OutboundDelivery.Origin.AI,
            status=OutboundDelivery.Status.PROCESSING,
            text='Ответ ИИ',
        )
        outgoing = self.message(outgoing=True, text='Ответ администратора с телефона')

        register_provider_outgoing(outgoing.id)

        self.chat.refresh_from_db()
        self.assertGreater(self.chat.ai_paused_until, timezone.now() + timedelta(minutes=59))

    def test_matching_processing_ai_webhook_does_not_pause_chat(self):
        OutboundDelivery.objects.create(
            chat=self.chat,
            origin=OutboundDelivery.Origin.AI,
            status=OutboundDelivery.Status.PROCESSING,
            text='Ответ ИИ',
        )
        outgoing = self.message(outgoing=True, text='Ответ ИИ')

        register_provider_outgoing(outgoing.id, api_message=True)

        self.chat.refresh_from_db()
        self.assertIsNone(self.chat.ai_paused_until)

    def test_second_worker_does_not_call_ai_for_claimed_chat(self):
        incoming = self.message()
        ChatAIState.objects.create(
            chat=self.chat,
            source_message=incoming,
            generation=1,
            processing=True,
        )

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'already-processing')
        self.assertFalse(OutboundDelivery.objects.exists())

    @patch('crm_app.services.ai_assistant.request_ai_decision', side_effect=RuntimeError('temporary'))
    def test_failed_ai_call_releases_processing_claim(self, _decision):
        incoming = self.message()
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        with self.assertRaisesRegex(RuntimeError, 'temporary'):
            process_ai_reply(self.chat.id, 1)

        self.assertFalse(ChatAIState.objects.get(chat=self.chat).processing)

    def test_non_text_uses_fixed_fallback_and_requests_human(self):
        incoming = self.message(message_type=Message.MessageType.PHOTO, text='')
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'queued')
        delivery = OutboundDelivery.objects.get()
        self.assertEqual(delivery.origin, OutboundDelivery.Origin.AI)
        self.assertEqual(delivery.text, self.config.fallback_text)
        self.chat.refresh_from_db()
        self.assertTrue(self.chat.needs_human_attention)

    @patch('crm_app.services.ai_assistant.request_ai_decision', return_value=(None, False))
    def test_conversation_closing_message_can_finish_without_reply(self, decision):
        incoming = self.message(text='Спасибо, до свидания')
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'no-reply')
        self.assertFalse(OutboundDelivery.objects.exists())
        state = ChatAIState.objects.get(chat=self.chat)
        self.assertIsNone(state.source_message_id)
        self.assertEqual(state.replied_to_message_id, incoming.id)
        decision.assert_called_once()

    @patch(
        'crm_app.services.ai_assistant.request_ai_decision',
        return_value=('Здравствуйте! Чем могу помочь?', False),
    )
    def test_simple_greeting_is_decided_by_ai(self, decision):
        incoming = self.message(text='Дарова')
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'queued')
        self.assertEqual(OutboundDelivery.objects.get().text, 'Здравствуйте! Чем могу помочь?')
        self.assertFalse(self.chat.needs_human_attention)
        decision.assert_called_once()

    @patch('crm_app.services.ai_assistant.request_ai_decision', return_value=('Завтра в 10:00.', False))
    def test_acknowledgement_with_new_question_still_gets_answer(self, _decision):
        incoming = self.message(text='Хорошо, а когда можно прийти?')
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)

        self.assertEqual(process_ai_reply(self.chat.id, 1), 'queued')
        self.assertEqual(OutboundDelivery.objects.get().text, 'Завтра в 10:00.')

    @override_settings(
        VSEGPT_API_KEY='test-key',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    @patch('crm_app.services.ai_assistant.requests.post')
    def test_ai_context_contains_incoming_and_outgoing_messages(self, post):
        self.message(text='Какие у вас часы работы?')
        self.message(outgoing=True, text='Ранее мы говорили о расписании')
        self.message(text='Так до скольких?')
        post.return_value = Mock(
            status_code=200,
            json=lambda: {'choices': [{'message': {'content': '{"action":"answer","answer":"До 20:00."}'}}]},
        )
        post.return_value.raise_for_status = Mock()

        answer, needs_human = request_ai_decision(self.chat, self.config)
        sent_messages = post.call_args.kwargs['json']['messages']
        self.assertEqual(answer, 'До 20:00.')
        self.assertFalse(needs_human)
        self.assertTrue(any(item['role'] == 'assistant' for item in sent_messages))
        self.assertEqual(sent_messages[-1]['role'], 'user')
        self.assertEqual(post.call_args.kwargs['json']['response_format'], {'type': 'json_object'})

    @override_settings(
        VSEGPT_API_KEY='test-key',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    @patch('crm_app.services.ai_assistant.requests.post')
    def test_plain_text_provider_answer_is_not_replaced_with_handoff(self, post):
        cache.clear()
        self.message(text='Привет')
        post.return_value = Mock(
            status_code=200,
            json=lambda: {'choices': [{'message': {'content': 'Привет! Как дела?'}}]},
        )

        answer, needs_human = request_ai_decision(self.chat, self.config)

        self.assertEqual(answer, 'Привет! Как дела?')
        self.assertFalse(needs_human)

    @override_settings(
        VSEGPT_API_KEY='test-key',
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    @patch('crm_app.services.ai_assistant.requests.post')
    def test_general_conversation_does_not_require_company_information(self, post):
        cache.clear()
        self.config.company_information = ''
        self.config.save(update_fields=['company_information'])
        self.message(text='Как у тебя дела?')
        post.return_value = Mock(
            status_code=200,
            json=lambda: {'choices': [{'message': {'content': '{"action":"answer","answer":"Хорошо! Чем могу помочь?"}'}}]},
        )

        answer, needs_human = request_ai_decision(self.chat, self.config)

        self.assertEqual(answer, 'Хорошо! Чем могу помочь?')
        self.assertFalse(needs_human)

    def test_presence_disables_ai_without_override(self):
        user = get_user_model().objects.create_user('operator')
        OperatorPresenceSession.objects.create(
            user=user,
            tab_id=uuid.uuid4(),
            is_visible=True,
            last_active_at=timezone.now(),
        )
        incoming = self.message()
        ChatAIState.objects.create(chat=self.chat, source_message=incoming, generation=1)
        result = process_ai_reply(self.chat.id, 1)
        self.assertTrue(result.startswith('reschedule:'))
        self.assertFalse(OutboundDelivery.objects.exists())


class AISettingsAPITests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('admin', 'admin@example.test', 'pass')
        self.client.force_login(self.admin)

    def test_settings_are_disabled_by_default_and_can_be_updated(self):
        response = self.client.get(reverse('ai-settings'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])
        response = self.client.patch(
            reverse('ai-settings'),
            {'enabled': True, 'company_information': 'Только проверенные сведения.'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])

    def test_presence_keeps_online_override_until_operator_disables_it(self):
        config = AISettings.load()
        config.enabled = True
        config.online_override_enabled = True
        config.save()
        response = self.client.post(
            reverse('ai-presence'),
            {'tab_id': str(uuid.uuid4()), 'is_visible': True},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['online_override_enabled'])
        config.refresh_from_db()
        self.assertTrue(config.online_override_enabled)

    def test_background_tab_does_not_keep_operator_present(self):
        config = AISettings.load()
        tab_id = str(uuid.uuid4())
        active = self.client.post(
            reverse('ai-presence'),
            {'tab_id': tab_id, 'is_visible': True},
            content_type='application/json',
        )
        self.assertTrue(active.json()['operator_present'])

        background = self.client.post(
            reverse('ai-presence'),
            {'tab_id': tab_id, 'is_visible': False},
            content_type='application/json',
        )

        self.assertFalse(background.json()['operator_present'])
        self.assertFalse(operator_is_present(config))
        session = OperatorPresenceSession.objects.get(tab_id=tab_id)
        self.assertIsNotNone(session.last_active_at)
        self.assertIsNotNone(session.inactive_since)

    @patch('crm_app.tasks.process_ai_reply_task.apply_async')
    def test_admin_can_reset_chat_ai_pause(self, apply_async):
        account = TelegramAccount.objects.create(
            name='Telegram personal',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )
        chat = Chat.objects.create(
            telegram_account=account,
            telegram_id=123456,
            chat_type=Chat.ChatType.PRIVATE,
            ai_paused_until=timezone.now() + timedelta(hours=1),
        )
        incoming = Message.objects.create(
            chat=chat,
            telegram_id=10,
            text='Новый вопрос',
            is_outgoing=False,
            telegram_date=timezone.now(),
        )
        ChatAIState.objects.create(chat=chat, source_message=incoming, generation=3)
        config = AISettings.load()
        config.enabled = True
        config.save()

        response = self.client.post(reverse('chat-reset-ai-pause', kwargs={'pk': chat.pk}))

        self.assertEqual(response.status_code, 200)
        chat.refresh_from_db()
        self.assertIsNone(chat.ai_paused_until)
        apply_async.assert_called_once()

    def test_admin_can_disable_pause_and_enable_ai_for_one_chat(self):
        account = TelegramAccount.objects.create(
            name='Telegram personal modes',
            account_type=TelegramAccount.AccountType.PERSONAL,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )
        chat = Chat.objects.create(
            telegram_account=account,
            telegram_id=654321,
            chat_type=Chat.ChatType.PRIVATE,
        )

        disabled = self.client.post(
            reverse('chat-set-ai-mode', kwargs={'pk': chat.pk}),
            {'mode': 'disabled'},
            content_type='application/json',
        )
        self.assertEqual(disabled.status_code, 200)
        chat.refresh_from_db()
        self.assertTrue(chat.ai_disabled)
        self.assertIsNone(chat.ai_paused_until)

        paused = self.client.post(
            reverse('chat-set-ai-mode', kwargs={'pk': chat.pk}),
            {'mode': 'paused', 'hours': 6},
            content_type='application/json',
        )
        self.assertEqual(paused.status_code, 200)
        chat.refresh_from_db()
        self.assertFalse(chat.ai_disabled)
        self.assertGreater(chat.ai_paused_until, timezone.now() + timedelta(hours=5, minutes=59))

        enabled = self.client.post(
            reverse('chat-set-ai-mode', kwargs={'pk': chat.pk}),
            {'mode': 'enabled'},
            content_type='application/json',
        )
        self.assertEqual(enabled.status_code, 200)
        chat.refresh_from_db()
        self.assertFalse(chat.ai_disabled)
        self.assertIsNone(chat.ai_paused_until)

    def test_chat_serializer_exposes_distinct_ai_modes(self):
        account = TelegramAccount.objects.create(
            name='MAX modes', account_type=TelegramAccount.AccountType.MAX,
        )
        chat = Chat.objects.create(
            telegram_account=account,
            telegram_id=777,
            chat_type=Chat.ChatType.PRIVATE,
            ai_disabled=True,
        )
        runtime = {'enabled': True, 'operator_present': False, 'online_override_enabled': False}
        disabled = ChatSerializer(chat, context={'ai_runtime': runtime}).data
        self.assertEqual(disabled['ai_status'], 'disabled')
        self.assertFalse(disabled['ai_active'])

        chat.ai_disabled = False
        chat.ai_paused_until = timezone.now() + timedelta(hours=1)
        paused = ChatSerializer(chat, context={'ai_runtime': runtime}).data
        self.assertEqual(paused['ai_status'], 'paused')

    def test_admin_can_pause_disable_and_enable_ai_globally(self):
        config = AISettings.load()
        config.enabled = True
        config.save()

        paused = self.client.post(
            reverse('ai-global-mode'),
            {'mode': 'paused', 'hours': 3},
            content_type='application/json',
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()['global_status'], 'paused')
        self.assertFalse(paused.json()['effective_enabled'])
        config.refresh_from_db()
        self.assertTrue(config.enabled)
        self.assertGreater(config.paused_until, timezone.now() + timedelta(hours=2, minutes=59))

        disabled = self.client.post(
            reverse('ai-global-mode'),
            {'mode': 'disabled'},
            content_type='application/json',
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.json()['global_status'], 'disabled')
        config.refresh_from_db()
        self.assertFalse(config.enabled)
        self.assertIsNone(config.paused_until)

        enabled = self.client.post(
            reverse('ai-global-mode'),
            {'mode': 'enabled'},
            content_type='application/json',
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.json()['global_status'], 'active')
        self.assertTrue(enabled.json()['effective_enabled'])

    def test_chat_serializer_shows_global_pause(self):
        account = TelegramAccount.objects.create(
            name='WhatsApp global pause', account_type=TelegramAccount.AccountType.WHATSAPP,
        )
        chat = Chat.objects.create(
            telegram_account=account,
            telegram_id=778,
            chat_type=Chat.ChatType.PRIVATE,
        )
        runtime = {
            'enabled': False,
            'global_paused': True,
            'global_paused_until': timezone.now() + timedelta(hours=1),
            'operator_present': False,
            'online_override_enabled': False,
        }
        data = ChatSerializer(chat, context={'ai_runtime': runtime}).data
        self.assertEqual(data['ai_status'], 'global_paused')
        self.assertFalse(data['ai_active'])


class GoogleContactsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('contacts-owner', is_staff=True)
        self.integration = GoogleContactsIntegration.objects.create(user=self.user, refresh_token='refresh')
        self.account = TelegramAccount.objects.create(
            name='WhatsApp', account_type=TelegramAccount.AccountType.WHATSAPP,
        )
        self.chat = Chat.objects.create(
            telegram_id=79991234567,
            telegram_account=self.account,
            chat_type=Chat.ChatType.PRIVATE,
            title='System profile',
            metadata={'external_chat_id': '79991234567@c.us'},
        )

    def test_phone_normalization_and_local_matching(self):
        self.assertEqual(normalize_phone('+7 (999) 123-45-67'), '79991234567')
        contact = GoogleContact.objects.create(
            integration=self.integration,
            resource_name='people/1',
            display_name='Иван из Google',
            phone_number='+7 999 123-45-67',
            normalized_phone='79991234567',
        )
        self.assertEqual(match_chat_contact(self.chat), contact)
        self.chat.refresh_from_db()
        data = ChatSerializer(self.chat, context={'ai_runtime': {}}).data
        self.assertEqual(data['display_name'], 'Иван из Google')
        self.assertEqual(data['system_name'], 'System profile')

    def test_phone_can_be_extracted_from_chat_title(self):
        contact = GoogleContact.objects.create(
            integration=self.integration,
            resource_name='people/title-phone',
            display_name='Номер из имени',
            phone_number='+7 996 124-33-05',
            normalized_phone='79961243305',
        )
        chat = Chat.objects.create(
            telegram_account=self.account,
            telegram_id=90001,
            title='79961243305@c.us',
            chat_type=Chat.ChatType.PRIVATE,
            metadata={},
        )

        self.assertEqual(chat_phone(chat), '79961243305')
        self.assertEqual(match_chat_contact(chat), contact)

    def test_short_provider_id_in_title_is_not_treated_as_phone(self):
        chat = Chat.objects.create(
            telegram_account=self.account,
            telegram_id=90002,
            title='Пользователь 123456789',
            chat_type=Chat.ChatType.PRIVATE,
            metadata={},
        )

        self.assertEqual(chat_phone(chat), '')

    @patch('crm_app.tasks.sync_google_contacts_task.delay')
    def test_stale_interrupted_sync_can_be_started_again(self, delay):
        self.integration.sync_in_progress = True
        self.integration.save(update_fields=['sync_in_progress', 'updated_at'])
        GoogleContactsIntegration.objects.filter(pk=self.integration.pk).update(
            updated_at=timezone.now() - timedelta(minutes=11),
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('google-contacts-sync'))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'queued')
        delay.assert_called_once_with(self.integration.id)

    @override_settings(GOOGLE_CLIENT_ID='client', GOOGLE_CLIENT_SECRET='secret')
    @patch('crm_app.services.google_contacts.requests.get')
    @patch('crm_app.services.google_contacts.requests.post')
    def test_sync_uses_people_api_once_then_matches_locally(self, post, get):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {'access_token': 'access', 'expires_in': 3600},
        )
        post.return_value.raise_for_status = Mock()
        get.return_value = Mock(
            status_code=200,
            json=lambda: {
                'connections': [{
                    'resourceName': 'people/1',
                    'names': [{'displayName': 'Иван из Google'}],
                    'phoneNumbers': [{'value': '+7 999 123-45-67', 'canonicalForm': '+79991234567'}],
                }],
                'nextSyncToken': 'sync-1',
            },
        )
        get.return_value.raise_for_status = Mock()

        result = sync_contacts(self.integration.id)
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['matched_chats'], 1)
        self.assertEqual(get.call_count, 1)
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.google_contact.display_name, 'Иван из Google')
