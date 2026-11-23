import shutil

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from main import services
from main.models import (CourseEnrollment, DataImport, DataIssue, License, Participant, Registration,
                         SpecializationEnrollment)


class Command(BaseCommand):
    help = ("Delete ALL imported data (participants, licences, enrolments, registrations, issues, import history) and the "
            "stored upload files, so the next import starts fresh. Users, access scopes, regions and the specialization "
            "catalogue are kept. Run with --yes.")

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="confirm the deletion")

    def handle(self, *args, **opts):
        if not opts["yes"]:
            raise CommandError("This deletes all imported data. Re-run with --yes to confirm.")
        with transaction.atomic():
            for model in (DataIssue, CourseEnrollment, SpecializationEnrollment, License, Participant, Registration, DataImport):
                n, _ = model.objects.all().delete()
                self.stdout.write(f"{model.__name__}: {n} deleted")
        root = services.data_store().root
        if root.exists():
            for child in root.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS("All imported data and stored uploads removed. Upload all three files on the Import page."))
