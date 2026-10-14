import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .auth import sign_tutor_webhook
from .models import Child, ChildLastLocation, Device, Parent, ParentChild, TutorSession
from .partitions import rotate_partitions

TASHKENT = ZoneInfo('Asia/Tashkent')


def history_count(child_id):
    with connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM location_updates WHERE child_id = %s', [child_id])
        return cursor.fetchone()[0]


class LocationUpdateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        rotate_partitions()
        cls.child = Child.objects.create(full_name='Ali')
        cls.device = Device.objects.create(child=cls.child)

    def post(self, device_id, latitude, longitude, recorded_at):
        return APIClient().post(
            '/v1/location-updates',
            {'latitude': latitude, 'longitude': longitude, 'recorded_at': recorded_at.isoformat()},
            format='json',
            headers={'X-Device-Id': str(device_id)},
        )

    def test_stale_update_is_kept_in_history_but_does_not_replace_last_location(self):
        newer = timezone.now() - timedelta(minutes=1)
        older = newer - timedelta(minutes=30)

        self.assertEqual(self.post(self.device.id, 41.31, 69.28, newer).json(), {'stored': True, 'is_latest': True})
        # The phone was offline and delivers an old point after the new one.
        response = self.post(self.device.id, 40.00, 68.00, older)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'stored': True, 'is_latest': False})
        last = ChildLastLocation.objects.get(child=self.child)
        self.assertEqual((last.latitude, last.longitude, last.recorded_at), (41.31, 69.28, newer))
        self.assertEqual(history_count(self.child.id), 2)

    def test_repeated_update_is_stored_once(self):
        recorded_at = timezone.now() - timedelta(minutes=1)

        first = self.post(self.device.id, 41.31, 69.28, recorded_at)
        second = self.post(self.device.id, 41.31, 69.28, recorded_at)

        self.assertEqual(first.json(), {'stored': True, 'is_latest': True})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {'stored': False, 'is_latest': False})
        self.assertEqual(history_count(self.child.id), 1)

    def test_timestamp_from_the_future_is_rejected(self):
        response = self.post(self.device.id, 41.31, 69.28, timezone.now() + timedelta(hours=1))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ChildLastLocation.objects.exists())

    def test_deactivated_or_unknown_device_is_rejected(self):
        old_device = Device.objects.create(child=Child.objects.create(full_name='Vali'), is_active=False)

        for device_id in (old_device.id, 999_999):
            response = self.post(device_id, 41.31, 69.28, timezone.now())
            self.assertEqual(response.status_code, 403)
        self.assertFalse(ChildLastLocation.objects.exists())

    def test_parent_credentials_cannot_send_coordinates(self):
        parent = Parent.objects.create(full_name='Ota')
        response = APIClient().post(
            '/v1/location-updates',
            {'latitude': 41.31, 'longitude': 69.28, 'recorded_at': timezone.now().isoformat()},
            format='json',
            headers={'X-Parent-Id': str(parent.id)},
        )
        self.assertEqual(response.status_code, 403)


class OverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mother = Parent.objects.create(full_name='Ona')
        cls.father = Parent.objects.create(full_name='Ota')
        cls.stranger = Parent.objects.create(full_name='Begona')
        cls.ali = Child.objects.create(full_name='Ali')
        cls.vali = Child.objects.create(full_name='Vali')
        cls.other_child = Child.objects.create(full_name='Boshqa')
        ParentChild.objects.create(parent=cls.mother, child=cls.ali)
        ParentChild.objects.create(parent=cls.mother, child=cls.vali)
        ParentChild.objects.create(parent=cls.father, child=cls.ali)
        ParentChild.objects.create(parent=cls.stranger, child=cls.other_child)

    def get(self, **headers):
        return APIClient().get('/v1/parent/children/overview', headers=headers)

    def test_parent_sees_only_linked_children_including_ones_without_data(self):
        recorded_at = timezone.now() - timedelta(minutes=5)
        ChildLastLocation.objects.create(
            child=self.ali, latitude=41.31, longitude=69.28, recorded_at=recorded_at, received_at=recorded_at
        )
        ChildLastLocation.objects.create(
            child=self.other_child, latitude=1, longitude=1, recorded_at=recorded_at, received_at=recorded_at
        )

        response = self.get(**{'X-Parent-Id': str(self.mother.id)})

        self.assertEqual(response.status_code, 200)
        children = response.json()['children']
        self.assertEqual([c['child_id'] for c in children], [self.ali.id, self.vali.id])
        self.assertEqual(children[0]['last_location']['latitude'], 41.31)
        self.assertEqual(children[1], {
            'child_id': self.vali.id, 'full_name': 'Vali', 'last_location': None, 'tutor_seconds_today': 0,
        })

    def test_both_parents_see_a_shared_child(self):
        father_view = self.get(**{'X-Parent-Id': str(self.father.id)}).json()['children']
        self.assertEqual([c['child_id'] for c in father_view], [self.ali.id])

    def test_anonymous_and_device_requests_are_denied(self):
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get(**{'X-Device-Id': '1'}).status_code, 403)

    def test_tutor_time_counts_only_completed_sessions_and_only_todays_part(self):
        def at(day, hour, minute):
            return datetime(2026, 10, day, hour, minute, tzinfo=TASHKENT)

        def session(external_id, start, end, status=TutorSession.Status.COMPLETED, child=self.ali):
            TutorSession.objects.create(
                child=child, external_id=external_id, status=status, started_at=start, ended_at=end
            )

        session('crosses-midnight', at(13, 23, 30), at(14, 0, 20))  # 20 min belong to the 14th
        session('today', at(14, 9, 0), at(14, 9, 30))  # 30 min
        session('yesterday', at(13, 15, 0), at(13, 16, 0))  # 0
        session('running', at(14, 9, 40), None, status=TutorSession.Status.IN_PROGRESS)  # 0
        session('other-child', at(14, 8, 0), at(14, 9, 0), child=self.vali)

        # 02:00 in Tashkent is still the 13th in UTC: catches a "today in UTC" bug.
        overview = services.get_children_overview(parent_id=self.mother.id, now=at(14, 2, 0))

        seconds = {c['child_id']: c['tutor_seconds_today'] for c in overview}
        self.assertEqual(seconds, {self.ali.id: 50 * 60, self.vali.id: 60 * 60})


class ConcurrentLocationUpdateTests(TransactionTestCase):
    """Real concurrent transactions (TestCase would wrap everything in one transaction).

    Each thread gets its own database connection; a barrier releases them at the same
    moment so the statements genuinely race inside Postgres.
    """

    WORKERS = 16

    def setUp(self):
        rotate_partitions()
        self.child = Child.objects.create(full_name='Ali')
        self.device = Device.objects.create(child=self.child)

    def tearDown(self):
        # location_updates is not a Django model, so the test flush does not clean it.
        with connection.cursor() as cursor:
            cursor.execute('TRUNCATE location_updates')

    def run_concurrently(self, timestamps):
        barrier = threading.Barrier(len(timestamps))

        def send(recorded_at):
            try:
                barrier.wait()
                return services.record_location(
                    device_id=self.device.id, latitude=41.0, longitude=69.0, recorded_at=recorded_at
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=len(timestamps)) as pool:
            return list(pool.map(send, timestamps))

    def test_racing_updates_leave_the_newest_point_as_last_location(self):
        base = timezone.now() - timedelta(hours=1)
        # Shuffled order so the newest point is not simply the last one submitted.
        timestamps = [base + timedelta(seconds=(i * 7) % self.WORKERS) for i in range(self.WORKERS)]

        self.run_concurrently(timestamps)

        last = ChildLastLocation.objects.get(child=self.child)
        self.assertEqual(last.recorded_at, max(timestamps))
        self.assertEqual(history_count(self.child.id), self.WORKERS)

    def test_racing_duplicates_are_stored_exactly_once(self):
        recorded_at = timezone.now() - timedelta(minutes=1)

        results = self.run_concurrently([recorded_at] * self.WORKERS)

        self.assertEqual(sum(r.stored for r in results), 1)
        self.assertEqual(sum(r.is_latest for r in results), 1)
        self.assertEqual(history_count(self.child.id), 1)


class TutorWebhookTests(TestCase):
    URL = '/v1/webhooks/ai-tutor/sessions'

    @classmethod
    def setUpTestData(cls):
        cls.child = Child.objects.create(full_name='Ali')
        cls.started = timezone.now() - timedelta(hours=1)
        cls.ended = cls.started + timedelta(minutes=25)

    def send(self, payload, secret=None, timestamp=None):
        body = json.dumps(payload).encode()
        timestamp = timestamp or int(time.time())
        signature = sign_tutor_webhook(body, timestamp, secret or settings.TUTOR_WEBHOOK_SECRET)
        return APIClient().post(
            self.URL,
            body,
            content_type='application/json',
            headers={'X-Tutor-Timestamp': str(timestamp), 'X-Tutor-Signature': signature},
        )

    def event(self, status, ended_at=None, **overrides):
        payload = {
            'external_id': 'sess-1',
            'child_id': self.child.id,
            'status': status,
            'started_at': self.started.isoformat(),
            'ended_at': ended_at.isoformat() if ended_at else None,
        }
        return payload | overrides

    def test_duplicate_completed_event_is_applied_once(self):
        first = self.send(self.event('completed', self.ended))
        second = self.send(self.event('completed', self.ended))

        self.assertEqual(first.json(), {'applied': True})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {'applied': False})
        self.assertEqual(TutorSession.objects.count(), 1)

    def test_late_in_progress_event_does_not_reopen_a_completed_session(self):
        self.send(self.event('completed', self.ended))
        response = self.send(self.event('in_progress'))

        self.assertEqual(response.json(), {'applied': False})
        session = TutorSession.objects.get(external_id='sess-1')
        self.assertEqual((session.status, session.ended_at), ('completed', self.ended))

    def test_in_progress_then_completed_finishes_the_session(self):
        self.send(self.event('in_progress'))
        response = self.send(self.event('completed', self.ended))

        self.assertEqual(response.json(), {'applied': True})
        self.assertEqual(TutorSession.objects.get(external_id='sess-1').status, 'completed')

    def test_event_cannot_move_a_session_to_another_child(self):
        other = Child.objects.create(full_name='Vali')
        self.send(self.event('in_progress'))

        response = self.send(self.event('completed', self.ended, child_id=other.id))

        self.assertEqual(response.json(), {'applied': False})
        self.assertEqual(TutorSession.objects.get(external_id='sess-1').child_id, self.child.id)

    def test_unknown_child_is_rejected(self):
        response = self.send(self.event('completed', self.ended, child_id=999_999))
        self.assertEqual(response.status_code, 400)

    def test_wrong_signature_and_stale_timestamp_are_rejected(self):
        wrong_secret = self.send(self.event('completed', self.ended), secret='attacker')
        replayed = self.send(self.event('completed', self.ended), timestamp=int(time.time()) - 3600)

        self.assertEqual(wrong_secret.status_code, 401)
        self.assertEqual(replayed.status_code, 401)
        self.assertFalse(TutorSession.objects.exists())

    def test_completed_without_end_is_rejected(self):
        response = self.send(self.event('completed'))
        self.assertEqual(response.status_code, 400)
