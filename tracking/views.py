from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .auth import IsDevice, IsParent, TutorWebhookAuthentication
from .models import TutorSession


class LocationUpdateSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    recorded_at = serializers.DateTimeField(help_text='When the device took the fix (device clock, ISO 8601).')

    def validate_recorded_at(self, value):
        now = timezone.now()
        if value > now + timedelta(seconds=settings.LOCATION_MAX_CLOCK_SKEW_SECONDS):
            raise serializers.ValidationError('recorded_at is in the future.')
        if value < now - timedelta(days=settings.LOCATION_RETENTION_DAYS):
            raise serializers.ValidationError('recorded_at is older than the retention period.')
        return value


class LocationUpdateResultSerializer(serializers.Serializer):
    stored = serializers.BooleanField(help_text='False when this exact update was already received (a retry).')
    is_latest = serializers.BooleanField(help_text='False when a newer location is already known.')


class LastLocationSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    recorded_at = serializers.DateTimeField()


class ChildOverviewSerializer(serializers.Serializer):
    child_id = serializers.IntegerField()
    full_name = serializers.CharField()
    last_location = LastLocationSerializer(allow_null=True, help_text='Null if the child never sent a location.')
    tutor_seconds_today = serializers.IntegerField(
        help_text='Completed AI tutor time today (Asia/Tashkent), only the part after midnight.'
    )


class ChildrenOverviewSerializer(serializers.Serializer):
    children = ChildOverviewSerializer(many=True)


class TutorSessionEventSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=64, help_text='Session id on the AI tutor provider side.')
    child_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=TutorSession.Status.choices)
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True, default=None, help_text='Required when completed.')

    def validate(self, attrs):
        if attrs['status'] == TutorSession.Status.COMPLETED and attrs['ended_at'] is None:
            raise serializers.ValidationError({'ended_at': 'Required for a completed session.'})
        if attrs['ended_at'] is not None and attrs['ended_at'] <= attrs['started_at']:
            raise serializers.ValidationError({'ended_at': 'Must be after started_at.'})
        return attrs


class TutorSessionEventResultSerializer(serializers.Serializer):
    applied = serializers.BooleanField(help_text='False for a duplicate, stale or conflicting event.')


UNAUTHENTICATED = OpenApiResponse(description='No or invalid credentials.')


class LocationUpdateView(APIView):
    permission_classes = [IsDevice]

    @extend_schema(
        tags=['Location'],
        summary='Receive a coordinate from the child device',
        description=(
            'Idempotent. The child is resolved from the authenticated device. '
            'A repeated update is not stored twice; an older update is kept in the 7-day '
            'history but never replaces a newer last location. Both still return 200, '
            'so the phone stops retrying.'
        ),
        request=LocationUpdateSerializer,
        responses={
            200: LocationUpdateResultSerializer,
            400: OpenApiResponse(description='Invalid coordinates, or recorded_at in the future / older than 7 days.'),
            401: UNAUTHENTICATED,
            403: OpenApiResponse(description='Not a device, or the device is inactive / not bound to a child.'),
        },
        examples=[
            OpenApiExample(
                'Location',
                value={'latitude': 41.311081, 'longitude': 69.240562, 'recorded_at': '2026-10-14T05:40:53Z'},
                request_only=True,
            ),
            OpenApiExample('New point', value={'stored': True, 'is_latest': True}, response_only=True),
            OpenApiExample('Late point after offline', value={'stored': True, 'is_latest': False}, response_only=True),
            OpenApiExample('Retry of the same point', value={'stored': False, 'is_latest': False}, response_only=True),
        ],
    )
    def post(self, request):
        serializer = LocationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.record_location(device_id=request.user.id, **serializer.validated_data)
        except services.DeviceNotAllowed:
            raise PermissionDenied('Device is not bound to a child.')
        # A duplicate or a stale point is still a success: the phone must stop retrying it.
        return Response({'stored': result.stored, 'is_latest': result.is_latest})


class ParentChildrenOverviewView(APIView):
    permission_classes = [IsParent]

    @extend_schema(
        tags=['Parent'],
        summary="Overview of the parent's children",
        description=(
            'Only children linked to the authenticated parent. Children without a location '
            'or tutor sessions are included with null / 0.'
        ),
        responses={
            200: ChildrenOverviewSerializer,
            401: UNAUTHENTICATED,
            403: OpenApiResponse(description='Credentials are not a parent.'),
        },
    )
    def get(self, request):
        # parent_id comes only from the auth layer; there is no parameter to tamper with.
        return Response({'children': services.get_children_overview(parent_id=request.user.id)})


class TutorSessionWebhookView(APIView):
    authentication_classes = [TutorWebhookAuthentication]

    @extend_schema(
        tags=['Webhooks'],
        summary='AI tutor session event',
        description=(
            'Called by the AI tutor provider. Safe to deliver late, out of order or more than once: '
            'events are upserted by external_id and "completed" is terminal. '
            'Signature: X-Tutor-Signature = hex(HMAC-SHA256(secret, "<X-Tutor-Timestamp>.<raw body>")).'
        ),
        parameters=[
            OpenApiParameter(
                'X-Tutor-Timestamp',
                int,
                OpenApiParameter.HEADER,
                required=True,
                description='Unix seconds; must be within 5 minutes of server time.',
            ),
        ],
        request=TutorSessionEventSerializer,
        responses={
            200: TutorSessionEventResultSerializer,
            400: OpenApiResponse(description='Invalid payload or unknown child.'),
            401: OpenApiResponse(description='Missing or invalid signature, or stale timestamp.'),
        },
        examples=[
            OpenApiExample(
                'Completed session',
                value={
                    'external_id': 'sess-123',
                    'child_id': 3,
                    'status': 'completed',
                    'started_at': '2026-10-14T03:40:00Z',
                    'ended_at': '2026-10-14T04:10:00Z',
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = TutorSessionEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            applied = services.apply_tutor_session_event(**serializer.validated_data)
        except services.UnknownChild:
            raise ValidationError({'child_id': 'Unknown child.'})
        # 200 for duplicates too: otherwise the provider keeps retrying the same event.
        return Response({'applied': applied})
