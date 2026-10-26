"""Framework-free unit tests for the business rules.  Run with either:
      python -m unittest main.logic.tests_logic        (no Django needed)
      python manage.py test                             (discovered automatically)
"""
import unittest
from datetime import date, datetime, timedelta, timezone

from . import catalog as C
from . import metrics as M
from . import normalize as N
from . import pipeline as P

UTC = timezone.utc
AS_OF = date(2026, 9, 16)


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class NormalizeTests(unittest.TestCase):
    def test_cnic_variants(self):
        self.assertEqual(N.norm_cnic("42101-1234567-1"), "4210112345671")
        self.assertEqual(N.norm_cnic(4210112345671), "4210112345671")
        self.assertEqual(N.norm_cnic(4210112345671.0), "4210112345671")
        self.assertEqual(N.norm_cnic("4210112345671"), "4210112345671")
        self.assertIsNone(N.norm_cnic("12345"))
        self.assertIsNone(N.norm_cnic(None))
        self.assertIsNone(N.norm_cnic(float("nan")))

    def test_email(self):
        self.assertEqual(N.norm_email("  Foo.Bar@Gmail.COM "), "foo.bar@gmail.com")
        self.assertIsNone(N.norm_email("not-an-email"))
        self.assertIsNone(N.norm_email(""))

    def test_dates(self):
        self.assertEqual(N.parse_dt("2026-09-14T12:05:04.525Z"), datetime(2026, 9, 14, 12, 5, 4, 525000, tzinfo=UTC))
        self.assertEqual(N.parse_dt("2026-09-04 10:05:44.106000").tzinfo, UTC)
        self.assertIsNone(N.parse_dt(datetime(972, 11, 11)))   # impossible DOB in the real data
        self.assertIsNone(N.parse_dt(""))

    def test_stable_id_is_stable_and_hides_cnic(self):
        a = N.stable_id("4210112345671")
        self.assertEqual(a, N.stable_id("4210112345671"))
        self.assertNotIn("4210112345671", a)


class CatalogTests(unittest.TestCase):
    def test_regions(self):
        self.assertEqual(C.canon_region("Ishkoman & Puniyal Region"), "Ishkoman Puniyal")
        self.assertEqual(C.canon_region("Gupis & Yasin Region"), "Gupis Yasin")
        self.assertEqual(C.region_from_program("AKEPB - National Employee (Male)"), "National")
        self.assertEqual(C.region_from_program("AKEPB - Gilgit II (Female)"), "Gilgit")
        self.assertIsNone(C.canon_region("Mars"))

    def test_unknown_specialization_never_counts_as_professional_certificate(self):
        fam, cat, known = C.classify_specialization("some-new-thing", "Google Something New")
        self.assertFalse(known)
        self.assertNotEqual(cat, C.PC)

    def test_admin_override_wins(self):
        fam, cat, known = C.classify_specialization("google-ai", "Google AI", {"google-ai": {"family": "Google AI", "category": C.AI}})
        self.assertEqual(cat, C.AI)

    def test_localised_variant_shares_family(self):
        a = C.classify_specialization("google-digital-marketing-ecommerce", "x")[0]
        b = C.classify_specialization("certificado-de-carrera-de-google-marketing-digital-e-e-commerce", "y")[0]
        self.assertEqual(a, b)

    def test_brd_band_boundaries_lower_inclusive(self):
        cases = {None: C.NO_PC, 0: "Below 20%", 19.99: "Below 20%", 20: "20-49%", 49.99: "20-49%",
                 50: "50-74%", 74.99: "50-74%", 75: "75-99%", 99.99: "75-99%", 100: "100%"}
        for v, b in cases.items():
            self.assertEqual(C.brd_band(v), b, v)

    def test_excel_compatible_bands_upper_inclusive_like_2025_workbook(self):
        cases = {0: "On Zero %", 11.11: "<20%", 20: "<20%", 22.2: "<30%", 30: "<30%", 33.3: "<40%", 40: "<40%", 42.8: "<50%",
                 50: "<50%", 55.5: "<75%", 75: "<75%", 77.7: "<85%", 85.7: "<99%", 88.9: "<99%", 100: "On 100%"}
        for v, b in cases.items():
            self.assertEqual(C.fine_band(v), b, v)
        self.assertEqual(C.fine_band(3 * 100 / 10 + 1e-12), "<30%")   # float noise must not cross an edge

    def test_legacy_candidate_band(self):
        self.assertEqual(C.legacy_band(None, False), None)
        self.assertEqual(C.legacy_band(0, False), "0%")
        self.assertEqual(C.legacy_band(50, False), "<50%")      # exactly 50% is "<50%" in the Excel
        self.assertEqual(C.legacy_band(50.1, False), ">50%")
        self.assertEqual(C.legacy_band(10, True), "100%")

    def test_age(self):
        self.assertIsNone(C.compute_age(None, 2016, AS_OF))
        self.assertEqual(C.compute_age(None, 30, AS_OF), 30)
        self.assertEqual(C.compute_age(datetime(2000, 9, 17), None, AS_OF), 25)   # birthday tomorrow
        self.assertEqual(C.compute_age(datetime(2000, 9, 16), None, AS_OF), 26)
        self.assertEqual(C.age_bracket(25), "18-25")
        self.assertEqual(C.age_bracket(26), "26-35")


def lic(state="ACCEPTED", **kw):
    return {"state": state, "removed": False, "invited_at": dt("2026-08-24T10:00:00"), "joined_at": dt("2026-08-25T10:00:00"),
            "last_activity_at": None, "learning_hours": 0.0, **kw}


def spec(family="Data Analytics", category=C.PC, pct=0.0, completed=False, last="2026-09-15T10:00:00", **kw):
    return {"family": family, "category": category, "progress_pct": pct, "completed": completed,
            "enrolled_at": dt("2026-08-26T10:00:00"), "last_activity_at": dt(last), "completed_at": dt("2026-09-10T10:00:00") if completed else None, **kw}


class DeriveTests(unittest.TestCase):
    def d(self, licenses, specs=(), courses=(), **kw):
        return M.derive_participant(list(licenses), list(specs), list(courses), AS_OF, **kw)

    def test_rule3_ai_completion_does_not_count(self):
        r = self.d([lic()], [spec("AI Essentials", C.AI, 100, True), spec("Prompting Essentials", C.AI, 100, True)])
        self.assertEqual(r["status"], "NO_PC")
        self.assertFalse(r["pc_completed"])
        self.assertEqual(r["ai_completed"], 2)
        self.assertIsNone(r["pc_progress"])

    def test_rule2_many_ai_courses_do_not_inflate(self):
        r = self.d([lic()], [spec("AI Essentials", C.AI, 100, True), spec("Data Analytics", C.PC, 25)])
        self.assertEqual(r["status"], "IN_PROGRESS")
        self.assertEqual(r["pc_progress"], 25.0)   # Rule 4: progress is the PC only

    def test_rule3_one_pc_completed_is_completed(self):
        r = self.d([lic()], [spec("Cybersecurity", C.PC, 100, True), spec("Data Analytics", C.PC, 10)])
        self.assertEqual(r["status"], "COMPLETED")
        self.assertEqual(r["pc_progress"], 100.0)
        self.assertEqual(r["pc_completed_families"], ["Cybersecurity"])

    def test_best_pc_is_primary(self):
        r = self.d([lic()], [spec("Data Analytics", C.PC, 10), spec("Project Management", C.PC, 60)])
        self.assertEqual(r["pc_family"], "Project Management")
        self.assertEqual(r["pc_progress"], 60.0)

    def test_pending_and_not_started(self):
        self.assertEqual(self.d([lic("PENDING")])["status"], "PENDING")
        r = self.d([lic("ACCEPTED")])
        self.assertEqual((r["status"], r["activity"]), ("NOT_STARTED", "NOT_STARTED"))
        self.assertEqual(self.d([])["status"], "NO_LICENSE")

    def test_activity_threshold_is_relative_to_snapshot_not_today(self):
        recent = self.d([lic()], [spec(last="2026-09-02T10:00:00")])      # exactly 14 days before 16 Sep -> active
        stale = self.d([lic()], [spec(last="2026-09-01T10:00:00")])       # 15 days -> inactive
        self.assertEqual(recent["activity"], "ACTIVE")
        self.assertEqual(stale["activity"], "INACTIVE")
        self.assertEqual(self.d([lic()], [spec(last="2026-09-01T10:00:00")], inactivity_days=30)["activity"], "ACTIVE")

    def test_course_only_learner_is_started_but_has_no_pc(self):
        r = self.d([lic()], [], [{"completed": True, "completed_at": None, "enrolled_at": dt("2026-09-01T00:00:00"), "last_activity_at": dt("2026-09-10T00:00:00")}])
        self.assertTrue(r["started"])
        self.assertEqual(r["status"], "NO_PC")
        self.assertEqual(r["courses_completed"], 1)

    def test_multiple_licences_count_once_accepted_wins(self):
        r = self.d([lic("PENDING"), lic("ACCEPTED")])
        self.assertEqual(r["license_state"], "ACCEPTED")
        self.assertEqual(r["license_count"], 2)

    def test_removed_licence(self):
        r = self.d([lic("ACCEPTED", removed=True)])
        self.assertEqual(r["license_state"], "REMOVED")
        self.assertEqual(r["status"], "OTHER")

    def test_partition_identity_holds_for_random_mix(self):
        import random
        random.seed(7)
        parts = []
        for i in range(400):
            st = random.choice(["ACCEPTED", "ACCEPTED", "PENDING"])
            specs = []
            if st == "ACCEPTED" and random.random() < .8:
                specs.append(spec(random.choice(["Data Analytics", "Cybersecurity"]), C.PC, random.choice([0, 12.5, 50, 100]),
                                  random.random() < .1, last=random.choice(["2026-09-15T00:00:00", "2026-08-20T00:00:00"])))
                if random.random() < .3:
                    specs.append(spec("AI Essentials", C.AI, 100, True))
            d = self.d([lic(st)], specs)
            d.update({"key": str(i), "shortlisted": True, "name": "x", "region": "R", "gender": "Male"})
            parts.append(d)
        k = M.kpis(parts, 0)
        self.assertEqual(k["accepted"], k["completed"] + k["active"] + k["inactive"] + k["not_started"])
        self.assertEqual(sum(1 for p in parts), sum(M.Counter(p["status"] for p in parts).values()))


class LegacyViewTests(unittest.TestCase):
    """The Excel-compatible numbers must follow the 2025 workbook's definitions (verified 18/18 + 99/99 against it)."""

    def part(self, specs, state="ACCEPTED", gender="Male"):
        d = M.derive_participant([lic(state)], specs, [], AS_OF)
        d.update({"key": str(id(specs)) + gender + state, "gender": gender, "shortlisted": True, "region": "R"})
        return d

    def test_excel_counts_ai_essentials_as_a_completion_but_brd_does_not(self):
        p = self.part([spec("AI Essentials", C.AI, 100, True)])
        rows = [{"participant_key": p["key"], "family": "AI Essentials", "category": C.AI, "completed": True, "progress_pct": 100.0}]
        L = M.legacy_kpis([p], rows)
        self.assertEqual((L["completed_candidates"], L["courses_completed"]), (1, 1))     # Excel rule
        self.assertEqual(M.kpis([p], 0)["completed"], 0)                                   # BRD Rule 3

    def test_started_means_specialization_row_and_invited_includes_pending(self):
        a = self.part([spec(pct=10)])
        b = self.part([], state="PENDING", gender="Female")
        L = M.legacy_kpis([a, b], [])
        self.assertEqual((L["invited"], L["started"], L["not_started"]), (2, 1, 1))
        self.assertEqual(L["gender"]["Female"], {"invited": 1, "started": 0, "not_started": 1})

    def test_candidate_counted_once_courses_counted_each(self):
        p = self.part([spec("Data Analytics", C.PC, 60), spec("Cybersecurity", C.PC, 10), spec("UX Design", C.PC, 0)])
        rows = [{"participant_key": p["key"], "family": f, "category": C.PC, "completed": False, "progress_pct": v}
                for f, v in (("Data Analytics", 60.0), ("Cybersecurity", 10.0), ("UX Design", 0.0))]
        L = M.legacy_kpis([p], rows)
        self.assertEqual((L["gt50_candidates"], L["lt50_candidates"], L["zero_candidates"]), (1, 0, 0))   # once, by best course
        self.assertEqual((L["gt50_courses"], L["lt50_courses"], L["zero_courses"]), (1, 1, 1))            # every course

    def test_in_progress_is_started_and_not_completed(self):
        a = self.part([spec(pct=10)])
        b = self.part([spec("AI Essentials", C.AI, 50)])
        c = self.part([spec("Cybersecurity", C.PC, 100, True)])
        k = M.kpis([a, b, c], 0)
        self.assertEqual((k["in_progress"], k["pc_in_progress"], k["completed"]), (2, 1, 1))


class TimeBucketTests(unittest.TestCase):
    def test_months_and_weeks_use_pakistan_time(self):
        late_utc = datetime(2026, 8, 31, 22, 30, tzinfo=UTC)      # = 1 Sept 03:30 in Pakistan
        self.assertEqual(M._month(late_utc), "2026-09")
        self.assertEqual(M._month(datetime(2026, 8, 31, 18, 30, tzinfo=UTC)), "2026-08")
        self.assertEqual(M._week(datetime(2026, 9, 6, 21, 0, tzinfo=UTC)), "2026-09-07")   # Sunday night UTC = Monday in Pakistan


class RankTests(unittest.TestCase):
    def test_dense_rank_and_ties(self):
        rows = [{"name": "A", "accepted": 10, "completion_rate": 5.0, "avg_progress": 10.0},
                {"name": "B", "accepted": 10, "completion_rate": 5.0, "avg_progress": 10.0},
                {"name": "C", "accepted": 10, "completion_rate": 1.0, "avg_progress": 3.0},
                {"name": "Z", "accepted": 0, "completion_rate": 0.0, "avg_progress": 0.0}]
        M.rank_rows(rows)
        ranks = {r["name"]: r["rank"] for r in rows}
        self.assertEqual((ranks["A"], ranks["B"], ranks["C"], ranks["Z"]), (1, 1, 3, None))


# ---------------------------------------------------------------- pipeline (identity resolution)
def master_row(i, cnic, email, name="Person", region="Southern Region", **kw):
    r = {"id": i, "Full Name": name, "Email Address": email, "Email Address (Course Access)": email, "Mobile Number": "+923000000000",
         "CNIC Number": cnic, "Gender": "Male", "Date of Birth": datetime(2000, 1, 1), "Age (completed years)": 26,
         "Currently Studying": "No", "Education Level": "Bachelors (Graduate)", "Profession": "Unemployed",
         "Residing Region": region, "Current Local Council": "Garden", "JK Attendance": "AMYNABAD",
         "Monthly family income": "30,001 to 50,000 PKR", "Which certification would you like to enrol in?": "Cybersecurity",
         "Created At": "2026-06-01 10:00:00"}
    r.update(kw)
    return r


def final_row(cnic, email, name="Person"):
    return {"Full Name": name, "Email Address (Course Access)": email, "CNIC Number": cnic, "Gender": "Male", "License Issued Category": "Southern"}


def prog_row(email, ext=None, state="MEMBER", program="AKEPB - Southern (Male)", name="Person"):
    return {"Name": name, "Email": email, "External ID": ext, "Program Name": program, "Member State": state,
            "Invitation Date": "2026-08-24T10:00:00Z", "Join Date": "2026-08-25T10:00:00Z" if state == "MEMBER" else None,
            "# Enrolled Courses": "0", "# Completed Courses": "0", "Learning Hours": "0"}


def spec_row(email, slug="google-cybersecurity", name="Google Cybersecurity", done="0", total="9", completed="No", enr="2026-08-26T00:00:00Z", **kw):
    r = {"Email": email, "Specialization": name, "Specialization Slug": slug, "# Completed Courses": done, "# Courses in Specialization": total,
         "Completed": completed, "Removed From Program": "No", "Enrollment Time": enr, "Last Specialization Activity Time": "2026-09-10T00:00:00Z",
         "Specialization Completion Time": "2026-09-12T00:00:00Z" if completed == "Yes" else None, "Specialization Certificate URL": None}
    r.update(kw)
    return r


def run(master, final, program, specs=(), courses=(), overrides=None):
    src = P.Sources(master_rows=master, final_sheets={"Southern": final}, coursera={"acctA": {"program": program, "spec": list(specs), "course": list(courses)}},
                    as_of=AS_OF, overrides=overrides or {})
    return P.build(src)


class PipelineTests(unittest.TestCase):
    C1, C2, C3 = "4210100000001", "4210100000002", "4210100000003"

    def codes(self, ds):
        return {i["code"] for i in ds["issues"]}

    def test_happy_path_links_all_three_datasets(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1)], [spec_row("a@x.com", done="3")])
        (p,) = ds["participants"]
        self.assertEqual((p["registration_form_id"], p["license_state"], p["status"], p["region"]), (1, "ACCEPTED", "IN_PROGRESS", "Southern"))
        self.assertAlmostEqual(p["pc_progress"], 33.33, places=1)
        self.assertEqual(ds["issues"], [])

    def test_email_beats_cnic_and_conflict_is_reported(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C2)])
        self.assertIn("LICENSE_CNIC_CONFLICT", self.codes(ds))
        self.assertEqual(len(ds["participants"]), 1)

    def test_cnic_fallback_when_email_differs(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("other@x.com", self.C1)])
        self.assertEqual(len(ds["participants"]), 1)
        self.assertEqual(ds["participants"][0]["license_state"], "ACCEPTED")
        self.assertIn("LICENSE_EMAIL_DIFFERS", self.codes(ds))

    def test_same_name_is_never_auto_merged_but_hinted(self):
        ds = run([master_row(1, self.C1, "a@x.com", name="Same Name"), master_row(2, self.C2, "b@x.com", name="Same Name")],
                 [final_row(self.C1, "a@x.com", "Same Name")], [prog_row("b@x.com", self.C2, name="Same Name")])
        self.assertEqual(len(ds["participants"]), 2)
        msg = " ".join(i["message"] for i in ds["issues"] if i["code"] == "SHORTLISTED_NO_LICENSE")
        self.assertIn("Possible match", msg)
        self.assertIn("LICENSE_NOT_SHORTLISTED", self.codes(ds))

    def test_duplicate_person_one_participant_two_licences(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com"), final_row(self.C1, "a2@x.com")],
                 [prog_row("a@x.com", self.C1), prog_row("a2@x.com", None, state="INVITED")])
        self.assertEqual(len(ds["participants"]), 1)
        self.assertEqual(ds["participants"][0]["license_count"], 2)
        self.assertEqual(ds["participants"][0]["license_state"], "ACCEPTED")
        self.assertIn("MULTIPLE_LICENSES", self.codes(ds))

    def test_orphan_licence_is_flagged_not_dropped(self):
        ds = run([], [], [prog_row("ghost@x.com")])
        (p,) = ds["participants"]
        self.assertFalse(p["shortlisted"])
        self.assertIn("LICENSE_UNIDENTIFIED", self.codes(ds))

    def test_manual_override_resolves_identity(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("typo@x.com")], overrides={("acctA", "typo@x.com"): self.C1})
        self.assertEqual(len(ds["participants"]), 1)
        self.assertEqual(ds["participants"][0]["license_state"], "ACCEPTED")

    def test_spec_versions_are_merged_once(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1)],
                 [spec_row("a@x.com", slug="google-ai", name="Google AI", done="7", total="7", completed="Yes"),
                  spec_row("a@x.com", slug="google-ai", name="Google AI", done="2", total="8", completed="No")])
        self.assertEqual(len(ds["specs"]), 1)
        self.assertTrue(ds["specs"][0]["completed"])
        self.assertEqual(ds["specs"][0]["progress_pct"], 100.0)
        self.assertEqual(ds["participants"][0]["status"], "COMPLETED")

    def test_completed_flag_with_partial_courses_is_counted_and_flagged(self):
        ds = run([master_row(1, self.C1, "a@x.com")], [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1)],
                 [spec_row("a@x.com", done="1", total="9", completed="Yes")])
        self.assertEqual(ds["participants"][0]["status"], "COMPLETED")
        self.assertIn("COMPLETED_PARTIAL", self.codes(ds))

    def test_registrations_always_add_up_across_regions(self):
        master = [master_row(1, self.C1, "a@x.com", region="Southern Region"), master_row(2, self.C2, "b@x.com", region="Hunza Region"),
                  master_row(3, self.C3, "c@x.com", region="Gilgit Region")]
        ds = run(master, [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1)])
        rows = M.group_table(ds["participants"], ds["registrations"], "region")
        self.assertEqual(sum(r["registrations"] for r in rows), 3)

    def test_region_is_residence_not_programme_and_national_staff_stay_in_their_region(self):
        master = [master_row(1, self.C1, "a@x.com", region="Hunza Region")]
        ds = run(master, [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1, program="AKEPB - National Employee (Male)")])
        (p,) = ds["participants"]
        self.assertEqual((p["region"], p["programme_region"]), ("Hunza", "National"))
        self.assertEqual(ds["registrations"][0]["region"], "Hunza")
        self.assertEqual(ds["registrations"][0]["programme_region"], "National")
        self.assertNotIn("RESIDENCE_PROGRAMME_MISMATCH", self.codes(ds))   # National programme is expected to differ

    def test_regional_programme_living_elsewhere_is_flagged(self):
        master = [master_row(1, self.C1, "a@x.com", region="Southern Region")]
        ds = run(master, [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1, program="AKEPB - Hunza (Male)")])
        self.assertEqual(ds["participants"][0]["region"], "Southern")
        self.assertIn("RESIDENCE_PROGRAMME_MISMATCH", self.codes(ds))

    def test_no_registration_falls_back_to_programme_region(self):
        ds = run([], [final_row(self.C1, "a@x.com")], [prog_row("a@x.com", self.C1, program="AKEPB - Hunza (Male)")])
        self.assertEqual(ds["participants"][0]["region"], "Hunza")

    def test_bad_cnic_in_final_list_is_reported(self):
        ds = run([], [final_row("123", "a@x.com")], [])
        self.assertIn("FINAL_BAD_CNIC", self.codes(ds))
        self.assertEqual(len(ds["participants"]), 1)   # kept, keyed by email


class SanitizeTests(unittest.TestCase):
    def test_formula_injection_neutralised_but_phone_numbers_survive(self):
        from .sanitize import cell_value
        self.assertEqual(cell_value('=HYPERLINK("http://evil")'), "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(cell_value("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(cell_value("-cmd|' /C calc'!A0"), "'-cmd|' /C calc'!A0")
        self.assertEqual(cell_value("+923001234567"), "+923001234567")        # phone numbers are NOT corrupted
        self.assertEqual(cell_value("+92 300 123-4567"), "+92 300 123-4567")
        self.assertEqual(cell_value("+cmd"), "'+cmd")
        self.assertEqual((cell_value(None), cell_value(True), cell_value(5)), ("", "Yes", 5))

    def test_sheet_title_is_valid_for_excel(self):
        from .sanitize import sheet_title
        t = sheet_title("Follow-up: Below 25% (Professional Certificate) [x]/y?")
        self.assertFalse(any(ch in t for ch in "[]:*?/\\"))
        self.assertLessEqual(len(t), 31)
        self.assertEqual(sheet_title(":::"), "---")


class GuardRailTests(unittest.TestCase):
    def test_duplicate_coursera_file_is_rejected(self):
        import csv, os, tempfile
        from . import loader
        tmp = tempfile.mkdtemp()
        name = "Program_Activity_gwg-ent-x_-_CourseraEnterpriseExport_2026-09-16_05-42-47_UTC.csv"
        for d in ("a", "b"):
            os.makedirs(os.path.join(tmp, d))
            with open(os.path.join(tmp, d, name), "w", newline="") as fh:
                w = csv.writer(fh); w.writerow(["Name", "Email", "Member State", "# Enrolled Courses"]); w.writerow(["A", "a@x.com", "MEMBER", "0"])
        with self.assertRaises(ValueError):
            loader.load_sources([os.path.join(tmp, "a", name), os.path.join(tmp, "b", name)])

    def test_partial_upload_raises_loud_alert(self):
        master = [master_row(i, f"42101000000{i:02d}", f"p{i}@x.com") for i in range(1, 21)]
        final = [final_row(f"42101000000{i:02d}", f"p{i}@x.com") for i in range(1, 21)]
        ds = run(master, final, [prog_row(f"p{i}@x.com", f"42101000000{i:02d}") for i in range(1, 11)])   # half the licences missing
        self.assertTrue(any("missing" in a for a in ds["stats"]["alerts"]))
        self.assertIn("POSSIBLE_MISSING_EXPORT", {i["code"] for i in ds["issues"]})

    def test_complete_upload_raises_no_alert(self):
        ds = run([master_row(1, "4210100000001", "a@x.com")], [final_row("4210100000001", "a@x.com")], [prog_row("a@x.com", "4210100000001")], [spec_row("a@x.com")])
        self.assertEqual(ds["stats"]["alerts"], [])

    def test_program_without_enrolment_files_is_flagged(self):
        ds = run([master_row(1, "4210100000001", "a@x.com")], [final_row("4210100000001", "a@x.com")], [prog_row("a@x.com", "4210100000001")])
        self.assertIn("ACCOUNT_NO_ENROLMENTS", {i["code"] for i in ds["issues"]})
        self.assertTrue(ds["stats"]["alerts"])

    def test_accounts_recorded_for_next_import_comparison(self):
        ds = run([], [], [prog_row("a@x.com")])
        self.assertEqual(ds["stats"]["accounts"], ["acctA"])


class ReconcileTests(unittest.TestCase):
    def test_reconcile_detects_mismatch(self):
        src = P.Sources([], {}, {"A": {"program": [prog_row("a@x.com")], "course": [], "spec": [],
                                        "summary": {"Current Members": "2", "Current Learners": "0", "Total Enrollments": "0",
                                                    "Average Progress": "0", "Total Completed Courses": "0"}}}, AS_OF)
        checks = P.reconcile(src)
        self.assertFalse(next(c for c in checks if c["label"].startswith("Current members"))["ok"])
        self.assertTrue(next(c for c in checks if c["label"].startswith("Total course"))["ok"])


if __name__ == "__main__":
    unittest.main()
