import glob
import os

from django.core.management.base import BaseCommand, CommandError

from main import services


class Command(BaseCommand):
    help = ("Import / refresh the GCC data from the registration master, final-list workbook and Coursera CSV exports. "
            "Example: python manage.py import_gcc data/*.xlsx data/*.csv")

    def add_arguments(self, parser):
        parser.add_argument("--allow-fewer-accounts", action="store_true", help="Do not stop if a Coursera account of the previous import is missing.")
        parser.add_argument("paths", nargs="+", help="Files or glob patterns (xlsx / csv). Types are detected from content.")

    def handle(self, *args, **opts):
        paths = []
        for p in opts["paths"]:
            paths += sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            raise CommandError("No files found.")
        try:
            imp = services.run_import(paths, allow_fewer_accounts=opts["allow_fewer_accounts"])
        except ValueError as e:
            raise CommandError(str(e))
        for f in imp.files:
            self.stdout.write("  " + f)
        self.stdout.write(self.style.SUCCESS(f"Imported. Data as of {imp.as_of}. Stats: {imp.stats}"))
        for a in imp.stats.get("alerts", []):
            self.stdout.write(self.style.ERROR("ALERT: " + a))
        bad = [c for c in imp.recon if c.get("ok") is False]
        if bad:
            self.stdout.write(self.style.WARNING(f"{len(bad)} reconciliation check(s) FAILED - see Data Quality page."))
        else:
            self.stdout.write(self.style.SUCCESS("All reconciliation checks against Coursera totals passed."))
