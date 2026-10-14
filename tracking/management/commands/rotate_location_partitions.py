from django.core.management.base import BaseCommand

from tracking.partitions import rotate_partitions


class Command(BaseCommand):
    help = 'Create upcoming location_updates partitions and drop expired ones. Run daily from cron.'

    def handle(self, *args, **options):
        rotate_partitions()
        self.stdout.write(self.style.SUCCESS('location_updates partitions rotated'))
