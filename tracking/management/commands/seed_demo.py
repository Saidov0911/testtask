from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tracking import services
from tracking.models import Child, Device, Parent, ParentChild, TutorSession

TASHKENT = ZoneInfo('Asia/Tashkent')


class Command(BaseCommand):
    help = 'Fill an empty database with demo parents, children, devices, locations and tutor sessions.'

    @transaction.atomic
    def handle(self, *args, **options):
        if Parent.objects.exists():
            raise CommandError('Database already has data; demo seed is meant for an empty database.')

        mother = Parent.objects.create(full_name='Malika Karimova')
        father = Parent.objects.create(full_name='Jasur Karimov')
        stranger = Parent.objects.create(full_name='Other Parent')

        ali = Child.objects.create(full_name='Ali Karimov')
        laylo = Child.objects.create(full_name='Laylo Karimova')
        other_child = Child.objects.create(full_name='Other Child')

        # Ali has two parents; Laylo only her mother; the stranger sees only their own child.
        ParentChild.objects.bulk_create([
            ParentChild(parent=mother, child=ali),
            ParentChild(parent=mother, child=laylo),
            ParentChild(parent=father, child=ali),
            ParentChild(parent=stranger, child=other_child),
        ])

        ali_device = Device.objects.create(child=ali)
        laylo_device = Device.objects.create(child=laylo)
        other_device = Device.objects.create(child=other_child)

        now = timezone.now()
        for minutes_ago, lat, lng in ((10, 41.3110, 69.2401), (5, 41.3123, 69.2450), (1, 41.3135, 69.2489)):
            services.record_location(
                device_id=ali_device.id, latitude=lat, longitude=lng,
                recorded_at=now - timedelta(minutes=minutes_ago),
            )
        services.record_location(
            device_id=other_device.id, latitude=39.6542, longitude=66.9597, recorded_at=now - timedelta(minutes=3)
        )
        # Laylo has a device but no location and no tutor sessions: still listed in the overview.

        today = timezone.localtime(now, TASHKENT).date()
        midnight = datetime.combine(today, datetime.min.time(), tzinfo=TASHKENT)
        TutorSession.objects.bulk_create([
            # Crosses midnight: only the 15 minutes after 00:00 count for today.
            TutorSession(child=ali, external_id='demo-crosses-midnight', status='completed',
                         started_at=midnight - timedelta(minutes=30), ended_at=midnight + timedelta(minutes=15)),
            TutorSession(child=ali, external_id='demo-morning', status='completed',
                         started_at=midnight + timedelta(hours=1), ended_at=midnight + timedelta(hours=1, minutes=20)),
            # Not finished yet: does not count.
            TutorSession(child=ali, external_id='demo-running', status='in_progress',
                         started_at=now - timedelta(minutes=5)),
        ])

        rows = [
            ('Parent', mother.id, 'X-Parent-Id', 'Malika: sees Ali and Laylo'),
            ('Parent', father.id, 'X-Parent-Id', 'Jasur: sees only Ali'),
            ('Parent', stranger.id, 'X-Parent-Id', 'Other parent: sees only Other Child'),
            ('Device', ali_device.id, 'X-Device-Id', "Ali's phone"),
            ('Device', laylo_device.id, 'X-Device-Id', "Laylo's phone"),
            ('Device', other_device.id, 'X-Device-Id', "Other Child's phone"),
        ]
        for kind, pk, header, note in rows:
            self.stdout.write(f'{kind:<7} {header}: {pk:<3} {note}')
        self.stdout.write(f'Child ids for the tutor webhook: Ali={ali.id}, Laylo={laylo.id}, Other={other_child.id}')
