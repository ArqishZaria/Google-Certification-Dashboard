from django.core.management.base import BaseCommand, CommandError

from main.logic import excel_parity


class Command(BaseCommand):
    help = "Recompute the 2025 Excel dashboard cards from its own Data sheet with the dashboard engine and compare."

    def add_arguments(self, parser):
        parser.add_argument("workbook", help="Path to 'GCC Dashboard as of 5th Nov 2025 - National.xlsx'")

    def handle(self, *args, **opts):
        try:
            rows = excel_parity.verify(opts["workbook"])
        except Exception as e:
            raise CommandError(str(e))
        self.stdout.write("cell  metric                                      Excel   Engine(default)   Engine(Excel-exact)")
        bad_default = bad_exact = 0
        for label, cell, expected, got, ok, got_x, ok_x in rows:
            self.stdout.write(f"{cell:5} {label:42} {expected!s:>6}   {got!s:>6} {'OK' if ok else 'DIFF':4}      {got_x!s:>6} {'OK' if ok_x else 'DIFF'}")
            bad_default += 0 if ok else 1
            bad_exact += 0 if ok_x else 1
        n = len(rows)
        self.stdout.write(self.style.SUCCESS(f"National cards, Excel-exact rule: {n - bad_exact}/{n} match"))
        reg = excel_parity.verify_regions(opts["workbook"])
        reg_bad = [r for r in reg if not r[4]]
        for region, label, expected, got, ok in reg_bad:
            self.stdout.write(self.style.ERROR(f"XX {region} {label}: Excel={expected} Engine={got}"))
        self.stdout.write(self.style.SUCCESS(f"Per-region cards (residing region): {len(reg) - len(reg_bad)}/{len(reg)} match"))
        self.stdout.write(f"Default rule (most advanced course): {n - bad_default}/{n} match - the 3 differences are the candidate-band cards, "
                          "where the Excel placed each candidate by their LAST sheet row (arbitrary).")
