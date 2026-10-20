from django.core.management.base import BaseCommand

from main import services


class Command(BaseCommand):
    help = "Recompute every participant's status/progress from stored enrolments (run after editing Specialization categories)."

    def handle(self, *args, **opts):
        self.stdout.write(self.style.SUCCESS(f"Recalculated {services.recompute_all()} participants."))
