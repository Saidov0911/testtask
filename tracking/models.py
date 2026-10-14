from django.db import models
from django.db.models import F, Q


class Parent(models.Model):
    full_name = models.CharField(max_length=255)

    class Meta:
        db_table = 'parents'


class Child(models.Model):
    full_name = models.CharField(max_length=255)
    parents = models.ManyToManyField(Parent, through='ParentChild', related_name='children')

    class Meta:
        db_table = 'children'


class ParentChild(models.Model):
    """M:N link. This table is the only source of truth for what a parent may see."""

    # No separate index on parent: the unique (parent, child) index already starts with it.
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, db_index=False)
    child = models.ForeignKey(Child, on_delete=models.CASCADE)

    class Meta:
        db_table = 'parent_children'
        constraints = [
            # Also serves the overview lookup "all children of parent X".
            models.UniqueConstraint(fields=['parent', 'child'], name='parent_children_uniq'),
        ]


class Device(models.Model):
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='devices')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'devices'
        constraints = [
            # A child reports from one device at a time; old devices stay as inactive rows.
            models.UniqueConstraint(
                fields=['child'], condition=Q(is_active=True), name='devices_one_active_per_child'
            ),
        ]


class ChildLastLocation(models.Model):
    """One row per child, so the overview never touches the location history."""

    child = models.OneToOneField(Child, on_delete=models.CASCADE, primary_key=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    recorded_at = models.DateTimeField()  # device clock: decides which point is newer
    received_at = models.DateTimeField()  # server clock: for debugging delays

    class Meta:
        db_table = 'child_last_location'
        constraints = [
            models.CheckConstraint(
                condition=Q(latitude__gte=-90, latitude__lte=90), name='last_location_lat_range'
            ),
            models.CheckConstraint(
                condition=Q(longitude__gte=-180, longitude__lte=180), name='last_location_lng_range'
            ),
        ]


class TutorSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress'
        COMPLETED = 'completed'

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='tutor_sessions')
    # Id from the AI tutor provider: makes a repeated or late webhook an idempotent upsert.
    external_id = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tutor_sessions'
        constraints = [
            # Declared here rather than unique=True, which would add a useless LIKE index.
            models.UniqueConstraint(fields=['external_id'], name='tutor_sessions_external_id_uniq'),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F('started_at')),
                name='tutor_sessions_ended_after_started',
            ),
            models.CheckConstraint(
                condition=~Q(status='completed') | Q(ended_at__isnull=False),
                name='tutor_sessions_completed_has_end',
            ),
        ]
        indexes = [
            # Overview asks "completed sessions of child X that ended after today's midnight".
            models.Index(
                fields=['child', 'ended_at'],
                condition=Q(status='completed'),
                name='tutor_sessions_completed_idx',
            ),
        ]


# location_updates (the 7-day history) is a partitioned table that the ORM cannot
# describe, so it is created with plain SQL in migration 0002.
