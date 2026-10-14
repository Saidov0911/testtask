from datetime import timedelta

from django.conf import settings
from django.utils import timezone
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
    recorded_at = serializers.DateTimeField()

    def validate_recorded_at(self, value):
        now = timezone.now()
        if value > now + timedelta(seconds=settings.LOCATION_MAX_CLOCK_SKEW_SECONDS):
            raise serializers.ValidationError('recorded_at is in the future.')
        if value < now - timedelta(days=settings.LOCATION_RETENTION_DAYS):
            raise serializers.ValidationError('recorded_at is older than the retention period.')
        return value


class LocationUpdateView(APIView):
    permission_classes = [IsDevice]

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

    def get(self, request):
        # parent_id comes only from the auth layer; there is no parameter to tamper with.
        return Response({'children': services.get_children_overview(parent_id=request.user.id)})


class TutorSessionEventSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=64)
    child_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=TutorSession.Status.choices)
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True, default=None)

    def validate(self, attrs):
        if attrs['status'] == TutorSession.Status.COMPLETED and attrs['ended_at'] is None:
            raise serializers.ValidationError({'ended_at': 'Required for a completed session.'})
        if attrs['ended_at'] is not None and attrs['ended_at'] <= attrs['started_at']:
            raise serializers.ValidationError({'ended_at': 'Must be after started_at.'})
        return attrs


class TutorSessionWebhookView(APIView):
    authentication_classes = [TutorWebhookAuthentication]

    def post(self, request):
        serializer = TutorSessionEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            applied = services.apply_tutor_session_event(**serializer.validated_data)
        except services.UnknownChild:
            raise ValidationError({'child_id': 'Unknown child.'})
        # 200 for duplicates too: otherwise the provider keeps retrying the same event.
        return Response({'applied': applied})
