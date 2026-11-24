"""Django integration tests: scope enforcement, import round-trip, views.  Run: python manage.py test"""
import csv
import os
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from . import services
from .logic.tests_logic import build_upload_files, master_row, prog_row, spec_row
from .models import Participant, Region, UserScope


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def make_files(tmp):
    from openpyxl import Workbook
    m = [master_row(1, "4210100000001", "a@x.com", region="Southern Region"),
         master_row(2, "7140100000002", "b@x.com", region="Hunza Region", **{"Current Local Council": "Altit & Karimabad", "JK Attendance": "ALTIT"})]
    wb = Workbook(); ws = wb.active; ws.title = "Certificate Applications"
    cols = list(m[0].keys()); ws.append(cols)
    for r in m:
        ws.append([r[c] for c in cols])
    wb.save(os.path.join(tmp, "master.xlsx"))
    wb = Workbook(); ws = wb.active; ws.title = "Southern"
    ws.append(["id", "License Issued Category", "Full Name", "Email Address (Course Access)", "CNIC Number", "Gender"])
    ws.append([1, "Southern", "Person", "a@x.com", "4210100000001", "Male"])
    ws2 = wb.create_sheet("Hunza")
    ws2.append(["id", "License Issued Category", "Full Name", "Email Address (Course Access)", "CNIC Number", "Gender"])
    ws2.append([2, "Hunza Region", "Person B", "b@x.com", "7140100000002", "Male"])
    wb.save(os.path.join(tmp, "final.xlsx"))
    acct = "gwg-ent-testacct"
    suffix = f"{acct}_-_CourseraEnterpriseExport_2026-09-16_05-42-47_UTC.csv"
    write_csv(os.path.join(tmp, f"Program_Activity_{suffix}"), [
        prog_row("a@x.com", "4210100000001"), prog_row("b@x.com", None, "INVITED", "AKEPB - Hunza (Male)")])
    write_csv(os.path.join(tmp, f"Specialisation_Activity_{suffix}"), [spec_row("a@x.com", done="9", total="9", completed="Yes")])
    write_csv(os.path.join(tmp, f"Course_Activity_{suffix}"), [{
        "Name": "Person", "Email": "a@x.com", "External ID": "4210100000001", "Course": "Foundations", "Course ID": "c1", "Course Slug": "f",
        "Enrollment Time": "2026-08-26T00:00:00Z", "Last Course Activity Time": "2026-09-10T00:00:00Z", "Overall Progress": "100",
        "Completed": "Yes", "Removed From Program": "No", "Completion Time": "2026-09-10T00:00:00Z", "Learning Hours": "3", "Course Type": "Course"}])
    return sorted(os.path.join(tmp, f) for f in os.listdir(tmp))


class ImportAndAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.paths = make_files(cls.tmp)
        cls.imp = services.run_import(cls.paths)

    def test_import_creates_linked_records(self):
        self.assertEqual(Participant.objects.count(), 2)
        a = Participant.objects.get(email="a@x.com")
        self.assertEqual((a.status, a.pc_progress, a.region.name), ("COMPLETED", 100.0, "Southern"))
        self.assertIsNotNone(a.registration)
        self.assertEqual(a.licenses.count(), 1)
        b = Participant.objects.get(email="b@x.com")
        self.assertEqual((b.status, b.license_state, b.region.name), ("PENDING", "PENDING", "Hunza"))

    def test_import_is_idempotent(self):
        services.run_import(self.paths)
        self.assertEqual(Participant.objects.count(), 2)

    def test_recompute_matches_import(self):
        before = {p.key: (p.status, p.pc_progress) for p in Participant.objects.all()}
        services.recompute_all()
        self.assertEqual(before, {p.key: (p.status, p.pc_progress) for p in Participant.objects.all()})

    def test_changing_category_changes_completion(self):
        from .models import Specialization
        Specialization.objects.filter(slug="google-cybersecurity").update(category="SHORT")
        services.recompute_all()
        self.assertEqual(Participant.objects.get(email="a@x.com").status, "NO_PC")

    def test_scope_regional_user_sees_only_own_region(self):
        u = User.objects.create_user("southchair", password="pw")
        UserScope.objects.create(user=u, role="REGIONAL_CHAIR", region=Region.objects.get(name="Southern"))
        self.client.login(username="southchair", password="pw")
        r = self.client.get(reverse("participants"))
        self.assertContains(r, "Person")
        self.assertNotContains(r, "Person B")
        # trying to widen the scope through the URL must not leak other regions
        r = self.client.get(reverse("participants") + "?region=Hunza")
        self.assertNotContains(r, "Person B")
        hunza = Participant.objects.get(email="b@x.com")
        self.assertEqual(self.client.get(reverse("participant_detail", args=[hunza.public_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("data_quality")).status_code, 403)

    def test_programme_filter_and_excel_view_render(self):
        User.objects.create_superuser("root2", "r2@x.com", "pw")
        self.client.login(username="root2", password="pw")
        r = self.client.get(reverse("dashboard") + "?programme=regional")
        self.assertContains(r, "Excel-compatible view")
        self.assertEqual(self.client.get(reverse("dashboard") + "?programme=national").status_code, 200)
        self.assertEqual(self.client.get(reverse("report_download", args=["excel-compatible", "xlsx"])).status_code, 200)

    def test_region_is_residence_based(self):
        self.assertEqual(Participant.objects.get(email="b@x.com").region.name, "Hunza")

    def test_user_without_scope_gets_403(self):
        User.objects.create_user("nobody", password="pw")
        self.client.login(username="nobody", password="pw")
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("report_download", args=["participant", "csv"])).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_superuser_sees_everything_and_downloads_work(self):
        User.objects.create_superuser("root", "r@x.com", "pw")
        self.client.login(username="root", password="pw")
        self.assertContains(self.client.get(reverse("dashboard")), "National Dashboard")
        for slug in ("national", "participant", "completion", "followup-pending"):
            for fmt in ("csv", "xlsx", "pdf"):
                self.assertEqual(self.client.get(reverse("report_download", args=[slug, fmt])).status_code, 200, (slug, fmt))
        self.assertEqual(self.client.get(reverse("followup") + "?list=pending").status_code, 200)
        self.assertEqual(self.client.get(reverse("data_quality")).status_code, 200)
        self.assertEqual(self.client.get(reverse("methodology")).status_code, 200)
        self.assertEqual(self.client.get(reverse("participants") + "?q=person&date_type=invited&date_from=garbage").status_code, 200)


class UploadPageTests(TestCase):
    """The simplified upload page: three upload types, templates, friendly errors, refresh of a single type."""

    def setUp(self):
        ctx = override_settings(GCC_DATA_DIR=tempfile.mkdtemp())      # every test gets its own empty store
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.tmp = tempfile.mkdtemp()
        self.master, self.final, self.zip = build_upload_files(self.tmp)
        User.objects.create_superuser("boss", "b@x.com", "pw")
        self.client.login(username="boss", password="pw")

    def up(self, path, name=None):
        with open(path, "rb") as fh:
            return SimpleUploadedFile(name or os.path.basename(path), fh.read())

    def test_page_shows_three_boxes_dropdown_and_template_buttons(self):
        r = self.client.get(reverse("import_data"))
        self.assertContains(r, "What do you want to upload?")
        for text in ("Master registration sheet", "Final list", "Coursera export", "Download Master template", "Download Final list template"):
            self.assertContains(r, text)

    def test_templates_download_for_staff_and_are_blocked_for_others(self):
        for kind, ctype in (("master", "spreadsheetml"), ("final", "spreadsheetml"), ("coursera", "zip")):
            r = self.client.get(reverse("import_template", args=[kind]))
            self.assertEqual(r.status_code, 200)
            self.assertIn(ctype, r["Content-Type"])
            self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("import_template", args=["nope"])).status_code, 404)
        User.objects.create_user("viewer", password="pw")
        self.client.login(username="viewer", password="pw")
        self.assertEqual(self.client.get(reverse("import_template", args=["master"])).status_code, 403)

    def test_wrong_file_in_wrong_box_changes_nothing_and_explains(self):
        r = self.client.post(reverse("import_data"), {"upload_type": "all", "master_file": self.up(self.final), "final_file": self.up(self.master)}, follow=True)
        self.assertContains(r, "looks like the FINAL LIST")
        self.assertEqual(Participant.objects.count(), 0)

    def test_first_import_needs_all_three_then_zip_only_refresh_works(self):
        r = self.client.post(reverse("import_data"), {"upload_type": "master", "master_file": self.up(self.master)}, follow=True)
        self.assertContains(r, "has not been uploaded yet")
        self.assertEqual(Participant.objects.count(), 0)
        r = self.client.post(reverse("import_data"), {"upload_type": "all", "master_file": self.up(self.master),
                                                     "final_file": self.up(self.final), "coursera_files": [self.up(self.zip)]})
        self.assertRedirects(r, reverse("data_quality"))
        self.assertEqual(Participant.objects.count(), 2)
        # later: only the Coursera ZIP is uploaded - master and final list come from the stored copies
        r = self.client.post(reverse("import_data"), {"upload_type": "coursera", "coursera_files": [self.up(self.zip)]})
        self.assertRedirects(r, reverse("data_quality"))
        self.assertEqual(Participant.objects.count(), 2)
        self.assertContains(self.client.get(reverse("import_data")), "Currently loaded")


class ResetCommandTests(TestCase):
    def test_reset_removes_imported_data_but_keeps_users(self):
        from django.core.management import call_command
        from .models import DataImport
        tmp = tempfile.mkdtemp()
        services.run_import(make_files(tmp))
        User.objects.create_user("keep", password="pw")
        self.assertGreater(Participant.objects.count(), 0)
        with self.assertRaises(Exception):
            call_command("reset_gcc_data")                      # needs --yes
        self.assertGreater(Participant.objects.count(), 0)
        call_command("reset_gcc_data", "--yes", verbosity=0)
        self.assertEqual((Participant.objects.count(), DataImport.objects.count()), (0, 0))
        self.assertTrue(User.objects.filter(username="keep").exists())


class StartPageTests(TestCase):
    def test_root_is_the_login_page_and_signed_in_users_go_to_dashboard(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'type="password"')
        User.objects.create_user("u", password="pw")
        self.client.login(username="u", password="pw")
        r = self.client.get("/")
        self.assertRedirects(r, "/dashboard/", fetch_redirect_response=False)

    def test_models_and_migrations_match(self):
        from django.core.management import call_command
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


class ResolveFlowTests(TestCase):
    """The Resolve queries page: queues, suggestions, every action, and that decisions change the numbers after Apply."""
    C1, C2 = "4210100000001", "4210100000002"

    @classmethod
    def setUpTestData(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = tempfile.mkdtemp()

    def setUp(self):
        from openpyxl import Workbook
        from .logic.tests_logic import final_row, master_row, prog_row
        self.settings_cm = override_settings(GCC_DATA_DIR=self.store)
        self.settings_cm.enable()
        self.addCleanup(self.settings_cm.disable)
        t = tempfile.mkdtemp()
        self.paths = {}

        def xlsx(name, sheets):
            wb = Workbook(); wb.remove(wb.active)
            for title, rows in sheets.items():
                ws = wb.create_sheet(title); cols = list(rows[0].keys()); ws.append(cols)
                for r in rows:
                    ws.append([r[c] for c in cols])
            p = os.path.join(t, name); wb.save(p); return p
        self.paths["master"] = xlsx("master.xlsx", {"Certificate Applications": [master_row(1, self.C1, "m1@x.com", name="Alice Khan"), master_row(2, self.C2, "m2@x.com", name="Bob Ali")]})
        # Alice's seat has a typo in the email; Bob is listed twice with two emails
        self.paths["final"] = xlsx("final.xlsx", {"Southern": [final_row(self.C1, "alice@x.cm", "Alice Khan"), final_row(self.C2, "bob@x.com", "Bob Ali"), final_row(self.C2, "bob2@x.com", "Bob Ali")]})
        suffix = "gwg-ent-test_-_CourseraEnterpriseExport_2026-11-07_08-27-27_UTC.csv"
        rows = [prog_row("alice@x.com", None, name="Alice Khan"), prog_row("bob@x.com", None, name="Bob Ali"), prog_row("zed@x.com", None, name="Zed Qureshi")]
        cp = os.path.join(t, f"Program_Activity_{suffix}")
        write_csv(cp, rows)
        sp = os.path.join(t, f"Specialisation_Activity_{suffix}")
        from .logic.tests_logic import spec_row
        write_csv(sp, [spec_row("bob@x.com")])
        cc = os.path.join(t, f"Course_Activity_{suffix}")
        write_csv(cc, [{"Name": "Bob", "Email": "bob@x.com", "External ID": "", "Course": "F", "Course ID": "c1", "Course Slug": "f", "Enrollment Time": "2026-08-26T00:00:00Z",
                        "Last Course Activity Time": "2026-09-10T00:00:00Z", "Overall Progress": "10", "Completed": "No", "Removed From Program": "No",
                        "Completion Time": "", "Learning Hours": "1", "Course Type": "Course"}])
        self.cour = [cp, sp, cc]
        services.import_components(None, master=self.paths["master"], final=self.paths["final"], coursera=self.cour)
        self.user = User.objects.create_superuser("boss", "b@x.com", "pw")
        self.client.force_login(self.user)

    def act(self, **kw):
        return self.client.post("/dashboard/resolve/act/", kw, follow=True)

    def apply(self):
        return self.client.post("/dashboard/resolve/apply/", follow=True)

    def seat(self, email):
        return Participant.objects.get(shortlisted=True, email=email)

    def test_page_lists_every_kind_of_case_and_suggests_the_match(self):
        r = self.client.get("/dashboard/resolve/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Seats without a Coursera licence")
        self.assertContains(r, "alice@x.cm")                       # the seat with the typo
        self.assertContains(r, "alice@x.com")                      # the licence that belongs to it, suggested
        self.assertContains(r, "Merge with this licence")
        self.assertContains(r, "zed@x.com")                        # a licence nobody is assigned to
        self.assertContains(r, "Same person listed with two emails")  # Bob twice

    def test_only_staff_may_use_the_page(self):
        User.objects.create_user("plain", password="pw")
        self.client.logout(); self.client.login(username="plain", password="pw")
        self.assertEqual(self.client.get("/dashboard/resolve/").status_code, 403)
        self.assertEqual(self.client.post("/dashboard/resolve/act/", {"action": "dismiss", "code": "X"}).status_code, 403)

    def test_merge_then_apply_gives_the_seat_its_licence(self):
        from .models import License
        seat, lic = self.seat("alice@x.cm"), License.objects.get(email="alice@x.com")
        self.assertEqual(seat.license_state, "NONE")
        self.act(action="merge", seat_id=seat.pk, lic_id=lic.pk)
        self.assertContains(self.client.get("/dashboard/resolve/"), "waiting for recalculation")
        self.apply()
        seat = Participant.objects.get(shortlisted=True, cnic=self.C1)
        self.assertEqual(seat.license_state, "ACCEPTED")
        self.assertEqual(seat.region.name, "Southern")
        self.assertNotContains(self.client.get("/dashboard/resolve/"), "Merge with this licence")

    def test_edit_email_then_apply_matches_the_licence(self):
        seat = self.seat("alice@x.cm")
        self.act(action="fix_email", seat_id=seat.pk, value="alice@x.com")
        self.apply()
        self.assertEqual(Participant.objects.get(shortlisted=True, cnic=self.C1).license_state, "ACCEPTED")
        # correcting the same seat again keeps working (the original Final-list email stays the key)
        seat = Participant.objects.get(shortlisted=True, cnic=self.C1)
        self.act(action="fix_email", seat_id=seat.pk, value="alice@x.com")
        self.apply()
        self.assertEqual(Participant.objects.get(shortlisted=True, cnic=self.C1).license_state, "ACCEPTED")

    def test_delete_seat_removes_the_duplicate(self):
        self.assertEqual(Participant.objects.filter(shortlisted=True, cnic=self.C2).count(), 2)
        self.act(action="remove_seat", seat_id=self.seat("bob2@x.com").pk)
        self.apply()
        self.assertEqual(Participant.objects.filter(shortlisted=True, cnic=self.C2).count(), 1)
        self.assertEqual(Participant.objects.get(shortlisted=True, cnic=self.C2).license_state, "ACCEPTED")

    def test_add_to_final_list_counts_the_licence_holder_in_that_region(self):
        from .models import License
        lic = License.objects.get(email="zed@x.com")
        self.assertFalse(Participant.objects.get(licenses=lic).shortlisted)
        self.act(action="add_seat", lic_id=lic.pk, region="Hunza", value="")
        self.apply()
        p = Participant.objects.get(licenses__email="zed@x.com")
        self.assertTrue(p.shortlisted)
        self.assertEqual(p.region.name, "Hunza")

    def test_mark_reviewed_hides_the_row_and_undo_brings_it_back(self):
        from .models import License
        lic = License.objects.get(email="zed@x.com")
        self.act(action="dismiss_licence", lic_id=lic.pk)
        self.assertNotContains(self.client.get("/dashboard/resolve/"), "Zed Qureshi")
        from .models import Dismissal
        self.act(action="undo", kind="dismiss", pk=Dismissal.objects.get().pk)
        self.assertContains(self.client.get("/dashboard/resolve/"), "Zed Qureshi")

    def test_bad_input_is_explained_not_a_crash(self):
        seat = self.seat("alice@x.cm")
        r = self.act(action="fix_email", seat_id=seat.pk, value="not-an-email")
        self.assertContains(r, "not a valid email")
        r = self.act(action="fix_cnic", seat_id=seat.pk, value="123")
        self.assertContains(r, "13 digits")
        r = self.act(action="merge", seat_id=99999, lic_id=99999)
        self.assertContains(r, "no longer exists")

    def test_decisions_survive_a_full_reimport(self):
        self.act(action="fix_email", seat_id=self.seat("alice@x.cm").pk, value="alice@x.com")
        services.import_components(None, master=self.paths["master"], final=self.paths["final"], coursera=self.cour)
        self.assertEqual(Participant.objects.get(shortlisted=True, cnic=self.C1).license_state, "ACCEPTED")
