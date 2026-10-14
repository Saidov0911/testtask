"""Stand-in for the ready-made authentication layer the task tells us to assume.

In production the identity comes from a verified token; here the gateway is assumed to
have verified it already and to pass the result in headers. The rest of the code only
depends on `request.user` being a Principal, never on these headers.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission


@dataclass(frozen=True)
class Principal:
    kind: str  # 'device' or 'parent'
    id: int
    is_authenticated: bool = True


class GatewayAuthentication(BaseAuthentication):
    def authenticate(self, request):
        for kind, header in (('device', 'X-Device-Id'), ('parent', 'X-Parent-Id')):
            value = request.headers.get(header)
            if value and value.isdigit():
                return Principal(kind=kind, id=int(value)), None
        return None

    def authenticate_header(self, request):
        return 'Bearer'  # makes DRF answer 401 instead of 403 for anonymous requests


class IsDevice(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'kind', None) == 'device'


class IsParent(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'kind', None) == 'parent'


def sign_tutor_webhook(body: bytes, timestamp: int, secret: str) -> str:
    message = f'{timestamp}.'.encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class TutorWebhookAuthentication(BaseAuthentication):
    """The AI tutor provider signs each webhook: HMAC-SHA256 over "<timestamp>.<raw body>".

    The timestamp is part of the signed message and must be fresh, so a captured request
    cannot be replayed hours later. Replays inside the window are harmless anyway,
    because applying the same event twice is a no-op.
    """

    def authenticate(self, request):
        timestamp = request.headers.get('X-Tutor-Timestamp', '')
        signature = request.headers.get('X-Tutor-Signature', '')
        if not timestamp.isdigit() or not signature:
            raise AuthenticationFailed('Missing webhook signature.')
        if abs(time.time() - int(timestamp)) > settings.TUTOR_WEBHOOK_MAX_AGE_SECONDS:
            raise AuthenticationFailed('Webhook timestamp is too old.')

        expected = sign_tutor_webhook(request.body, int(timestamp), settings.TUTOR_WEBHOOK_SECRET)
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationFailed('Invalid webhook signature.')
        return Principal(kind='tutor_provider', id=0), None

    def authenticate_header(self, request):
        return 'HMAC-SHA256'
