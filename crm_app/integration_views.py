import json
import logging

from django.http import HttpResponse

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TelegramAccount
from .services.jget_bridge import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify_signature
from .services.jget_ingestion import ingest_question


logger = logging.getLogger(__name__)


class JgetQuestionWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, account_id: int):
        try:
            account = TelegramAccount.objects.get(
                id=account_id,
                account_type=TelegramAccount.AccountType.BOT,
                status=TelegramAccount.AccountStatus.ACTIVE,
            )
        except TelegramAccount.DoesNotExist:
            return Response({'error': 'Integration not found'}, status=status.HTTP_404_NOT_FOUND)

        if not account.bridge_secret:
            logger.error('Bridge secret is not configured for Telegram account %s', account.id)
            return Response({'error': 'Integration is not configured'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        body = request.body
        timestamp = request.headers.get(TIMESTAMP_HEADER, '')
        signature = request.headers.get(SIGNATURE_HEADER, '')
        if not verify_signature(account.bridge_secret, timestamp, body, signature):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(body.decode('utf-8'))
            message, created, chat_created = ingest_question(account, payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Could not ingest JGET question for account %s', account.id)
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                'status': 'created' if created else 'duplicate',
                'message_id': message.id,
                'chat_id': message.chat_id,
                'chat_created': chat_created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

def _verify_hmac(secret, body, signature):
    import hashlib
    import hmac

    if not secret:
        return True
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


class WhatsAppWebhookView(APIView):
    """GREEN-API Webhook Endpoint receiver."""

    account_type = TelegramAccount.AccountType.WHATSAPP
    provider_slug = 'whatsapp'

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def _account(self, account_id):
        return TelegramAccount.objects.get(
            id=account_id,
            account_type=self.account_type,
            status=TelegramAccount.AccountStatus.ACTIVE,
        )

    @staticmethod
    def _expected_authorization(account):
        token = (account.green_webhook_token or '').strip()
        if token and not token.lower().startswith(('bearer ', 'basic ')):
            token = f'Bearer {token}'
        return token

    @staticmethod
    def _content(message_data):
        from .services.message_content import normalize_green_message
        normalized = normalize_green_message(message_data)
        return normalized['text'], normalized['download_url'], normalized['content']

    def get(self, request, account_id):
        try:
            account = self._account(account_id)
        except TelegramAccount.DoesNotExist:
            return Response({'error': 'Integration not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'provider': f'green-api-{self.provider_slug}',
            'configured': bool(account.green_api_instance_id and account.green_api_token),
        })

    def post(self, request, account_id):
        import hmac
        from datetime import datetime
        from django.utils import timezone
        from .models import Message
        from .services.provider_ingestion import ingest_provider_message
        from .services.realtime import publish_message
        from .tasks import download_green_api_media_task

        try:
            account = self._account(account_id)
        except TelegramAccount.DoesNotExist:
            return Response({'error': 'Integration not found'}, status=status.HTTP_404_NOT_FOUND)

        expected = self._expected_authorization(account)
        supplied = request.headers.get('Authorization', '')
        if expected and not hmac.compare_digest(expected, supplied):
            return Response({'error': 'Invalid webhook authorization'}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        instance_id = str((payload.get('instanceData') or {}).get('idInstance') or '')
        if account.green_api_instance_id and instance_id and instance_id != str(account.green_api_instance_id):
            logger.warning('Ignored GREEN-API webhook for instance %s on account %s', instance_id, account.id)
            return Response({'status': 'ignored', 'processed': 0})

        webhook_type = payload.get('typeWebhook')
        message_data = payload.get('messageData')
        message_data = message_data if isinstance(message_data, dict) else {}
        if (
            webhook_type in {'incomingMessageReceived', 'outgoingMessageReceived', 'outgoingAPIMessageReceived'}
            and message_data.get('typeMessage') == 'reactionMessage'
        ):
            from .services.reactions import set_actor_reaction

            sender = payload.get('senderData') or {}
            sender = sender if isinstance(sender, dict) else {}
            chat_id = sender.get('chatId') or sender.get('sender') or payload.get('chatId')
            quoted = message_data.get('quotedMessage') or {}
            quoted = quoted if isinstance(quoted, dict) else {}
            target_id = quoted.get('idMessage') or quoted.get('stanzaId')
            emoji, _, _ = self._content(message_data)
            target = Message.objects.filter(
                chat__telegram_account=account,
                chat__metadata__external_chat_id=str(chat_id),
                external_message_id=str(target_id),
            ).first() if chat_id and target_id else None
            if target:
                actor = 'self' if webhook_type != 'incomingMessageReceived' else f"peer:{sender.get('sender') or 'remote'}"
                target = set_actor_reaction(target.id, actor, emoji, chosen=actor == 'self')
                publish_message(target.id)
            return Response({'status': 'accepted', 'processed': int(bool(target))})

        message_webhook_types = {
            'incomingMessageReceived',
            'outgoingMessageReceived',
            'outgoingAPIMessageReceived',
        }
        if webhook_type in message_webhook_types:
            is_outgoing = webhook_type != 'incomingMessageReceived'
            sender = payload.get('senderData') or {}
            sender = sender if isinstance(sender, dict) else {}
            chat_id = sender.get('chatId') or sender.get('sender')
            raw_chat_type = str(sender.get('chatType') or '').lower()
            if account.account_type == TelegramAccount.AccountType.MAX:
                if raw_chat_type not in {'user', 'group', 'bot'}:
                    logger.info('Ignored MAX %s chat %s on account %s', raw_chat_type or 'unknown', chat_id, account.id)
                    return Response({'status': 'ignored', 'processed': 0})
                # GREEN-API MAX group identifiers are negative. Keep this fallback because
                # some webhook revisions have reported chatType=user for group traffic.
                chat_type = 'group' if raw_chat_type == 'group' or str(chat_id).startswith('-') else 'private'
            else:
                chat_id_text = str(chat_id or '').lower()
                if chat_id_text.endswith('@g.us'):
                    chat_type = 'group'
                elif chat_id_text.endswith(('@c.us', '@lid')):
                    chat_type = 'private'
                else:
                    logger.info('Ignored WhatsApp non-conversation chat %s on account %s', chat_id, account.id)
                    return Response({'status': 'ignored', 'processed': 0})
            peer_is_bot = raw_chat_type == 'bot' or bool(
                sender.get('isBot') or sender.get('is_bot') or sender.get('bot')
            )
            if account.account_type == TelegramAccount.AccountType.MAX:
                peer_is_bot = peer_is_bot or str(chat_id).lower().endswith('@bot')
            message_id = payload.get('idMessage')
            if not chat_id or not message_id:
                return Response({'error': 'Missing chatId or idMessage'}, status=status.HTTP_400_BAD_REQUEST)
            from .services.message_content import normalize_green_message
            normalized = normalize_green_message(message_data)
            text, download_url, content = (
                normalized['text'], normalized['download_url'], normalized['content']
            )
            try:
                event_time = datetime.fromtimestamp(int(payload.get('timestamp')), tz=timezone.get_current_timezone())
            except (TypeError, ValueError, OSError):
                event_time = timezone.now()
            raw_type = normalized['raw_type']
            is_mutation = raw_type in {'editedMessage', 'deletedMessage'}
            target_message_id = (
                content.get('stanzaId') or content.get('idMessage')
            ) if is_mutation else None
            quoted = message_data.get('quotedMessage') or (message_data.get('extendedTextMessageData') or {}).get('quotedMessage') or {}
            message, created, _ = ingest_provider_message(
                account=account,
                external_chat_id=chat_id,
                external_message_id=target_message_id or message_id,
                text=text,
                sender_id=sender.get('sender'),
                # For outgoing webhooks senderName is the connected account;
                # chatName is the peer visible in the conversation list.
                sender_name=(
                    sender.get('chatName') or sender.get('senderContactName')
                    if is_outgoing else
                    sender.get('senderName') or sender.get('senderContactName') or sender.get('chatName')
                ),
                occurred_at=event_time,
                message_type=normalized['message_type'],
                media_file_id=message_id if download_url else None,
                reply_to_external_message_id=quoted.get('idMessage') or quoted.get('stanzaId'),
                metadata={
                    'raw_type': raw_type, 'provider_content': content,
                    'special_content': normalized['special_content'],
                    'forward_info': normalized['forward_info'],
                    'download_url': download_url, 'external_chat_id': chat_id,
                    'green_webhook_type': webhook_type,
                },
                chat_type=chat_type,
                is_bot=peer_is_bot,
                is_outgoing=is_outgoing,
                status=Message.MessageStatus.SENT if is_outgoing else Message.MessageStatus.RECEIVED,
                update_existing=is_mutation,
            )
            if created and download_url:
                download_green_api_media_task.delay(message.id)
            TelegramAccount.objects.filter(pk=account.pk).update(last_activity=timezone.now(), last_error='')
            return Response({'status': 'accepted', 'processed': 1})

        if webhook_type == 'outgoingMessageStatus':
            message = Message.objects.filter(
                chat__telegram_account=account,
                external_message_id=payload.get('idMessage'),
            ).first()
            if message:
                provider_status = payload.get('status', '')
                message.status = Message.MessageStatus.FAILED if provider_status in {'failed', 'noAccount'} else Message.MessageStatus.SENT
                message.metadata = {**(message.metadata or {}), 'provider_status': provider_status, 'provider_status_payload': payload}
                message.save(update_fields=['status', 'metadata', 'updated_at'])
                publish_message(message.id)
            return Response({'status': 'accepted', 'processed': int(bool(message))})

        if webhook_type == 'stateInstanceChanged':
            state = payload.get('stateInstance') or ''
            TelegramAccount.objects.filter(pk=account.pk).update(
                last_activity=timezone.now(),
                last_error='' if state == 'authorized' else f'GREEN-API instance state: {state}',
            )
        return Response({'status': 'accepted', 'processed': 0})

class MaxWebhookView(WhatsAppWebhookView):
    """GREEN-API Webhook Endpoint receiver for a personal MAX account."""

    account_type = TelegramAccount.AccountType.MAX
    provider_slug = 'max'
