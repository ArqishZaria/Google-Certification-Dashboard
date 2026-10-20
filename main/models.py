"""Relational model for the GCC dashboard.

   Region 1--* LocalCouncil 1--* Area
   Registration (Master sheet)  1--0..1  Participant (one real person, keyed by CNIC)
   Participant 1--* License (Coursera membership)  1--* SpecializationEnrollment / CourseEnrollment
   Specialization (catalogue)  1--* SpecializationEnrollment

Participant carries denormalised *derived* fields (status, progress, ...). They are recomputed
by main.services.recompute_all() from the enrolment rows with the single rule-set in main/logic/metrics.py,
so they can never drift from the source rows.
"""
from django.conf import settings
from django.db import models

from .logic import catalog as C


class Region(models.Model):
    name = models.CharField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_national = models.BooleanField(default=False, help_text="The pseudo-region holding National Council staff licences.")

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class LocalCouncil(models.Model):
    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name="councils")
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["region__sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["region", "name"], name="uniq_council_per_region")]

    def __str__(self):
        return f"{self.name} ({self.region})"


class Area(models.Model):
    """Jamatkhana / area inside a local council."""
    local_council = models.ForeignKey(LocalCouncil, on_delete=models.PROTECT, related_name="areas")
    name = models.CharField(max_length=160)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["local_council", "name"], name="uniq_area_per_council")]

    def __str__(self):
        return self.name


class Specialization(models.Model):
    """Catalogue of Coursera specializations. EDIT `category` HERE to change what counts as a
    Professional Certificate, then press "Recalculate" on the Data Quality page."""
    CATEGORY_CHOICES = [(C.PC, "Professional Certificate"), (C.AI, "AI / Essentials course"), (C.SHORT, "Short course / other")]
    slug = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    family = models.CharField(max_length=120, help_text="Groups localised / versioned variants of the same programme.")
    category = models.CharField(max_length=8, choices=CATEGORY_CHOICES, default=C.SHORT)
    reviewed = models.BooleanField(default=True, help_text="False = new specialization seen in an export; please confirm its category.")

    class Meta:
        ordering = ["category", "family"]

    def __str__(self):
        return f"{self.family} [{self.category}]"


class DataImport(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    as_of = models.DateField(help_text="Data snapshot date (from the Coursera export file names).")
    files = models.JSONField(default=list)
    stats = models.JSONField(default=dict)
    recon = models.JSONField(default=list)
    inactivity_days = models.PositiveSmallIntegerField(default=14)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import {self.created_at:%Y-%m-%d %H:%M} (data as of {self.as_of})"


class Registration(models.Model):
    form_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    course_email = models.EmailField(blank=True, null=True)
    mobile = models.CharField(max_length=40, blank=True, null=True)
    cnic = models.CharField(max_length=13, blank=True, null=True, db_index=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    marital_status = models.CharField(max_length=30, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    age = models.PositiveSmallIntegerField(blank=True, null=True)
    origin_region = models.CharField(max_length=80, blank=True, null=True)
    currently_studying = models.CharField(max_length=10, blank=True, null=True)
    education_level = models.CharField(max_length=80, blank=True, null=True)
    profession = models.CharField(max_length=40, blank=True, null=True)
    occupation_type = models.CharField(max_length=200, blank=True, null=True)
    income_band = models.CharField(max_length=40, blank=True, null=True)
    preferred_label = models.CharField(max_length=120, blank=True, null=True)
    preferred_family = models.CharField(max_length=120, blank=True, null=True)
    prof_status = models.CharField(max_length=30, blank=True, null=True)
    education_group = models.CharField(max_length=40, blank=True, null=True)
    age_bracket = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    programme_region = models.CharField(max_length=60, blank=True, null=True, help_text="Coursera programme of the linked licence, e.g. 'National' for council staff.")
    # region = where the person lives (form field "Residing Region"); identical to residing_region, kept as the reporting dimension
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.PROTECT, related_name="registrations")
    residing_region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.PROTECT, related_name="residents")
    local_council = models.ForeignKey(LocalCouncil, null=True, blank=True, on_delete=models.PROTECT, related_name="registrations")
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.PROTECT, related_name="registrations")

    def __str__(self):
        return f"#{self.form_id} {self.name}"


class Participant(models.Model):
    # ---- identity
    key = models.CharField(max_length=80, unique=True, help_text="CNIC, or EMAIL:<address> when the CNIC is unknown.")
    public_id = models.CharField(max_length=12, unique=True)
    cnic = models.CharField(max_length=13, blank=True, null=True, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    email = models.EmailField(blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=40, blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    coursera_external_id = models.CharField(max_length=20, blank=True, null=True)
    registration = models.OneToOneField(Registration, null=True, blank=True, on_delete=models.SET_NULL, related_name="participant")
    # ---- geography / profile
    # region = RESIDING region from the registration form (BRD s.5 / 2025 Excel). Falls back to the Coursera programme
    # region only when there is no registration record. National Council staff are therefore counted where they live.
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.PROTECT, related_name="participants")
    programme_region = models.CharField(max_length=60, blank=True, null=True, db_index=True)
    residing_region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    local_council = models.ForeignKey(LocalCouncil, null=True, blank=True, on_delete=models.PROTECT, related_name="participants")
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.PROTECT, related_name="participants")
    shortlisted = models.BooleanField(default=False)
    shortlist_sheet = models.CharField(max_length=40, blank=True, null=True)
    issuance_note = models.CharField(max_length=200, blank=True, null=True)
    preferred_label = models.CharField(max_length=120, blank=True, null=True)
    preferred_family = models.CharField(max_length=120, blank=True, null=True)
    prof_status = models.CharField(max_length=30, default="Data not available")
    education_group = models.CharField(max_length=40, default="Data not available")
    age_bracket = models.CharField(max_length=20, default="Data not available")
    income_band = models.CharField(max_length=40, default="Data not available")
    accounts = models.JSONField(default=list)
    # ---- derived (recomputed from enrolments)
    license_state = models.CharField(max_length=10, db_index=True, default="NONE")
    license_count = models.PositiveSmallIntegerField(default=0)
    started = models.BooleanField(default=False)
    status = models.CharField(max_length=12, db_index=True, default="NO_LICENSE")
    activity = models.CharField(max_length=12, blank=True, null=True, db_index=True)
    pc_progress = models.FloatField(blank=True, null=True)
    pc_family = models.CharField(max_length=120, blank=True, null=True)
    pc_completed = models.BooleanField(default=False)
    pc_completed_at = models.DateTimeField(blank=True, null=True)
    pc_completed_families = models.JSONField(default=list)
    pc_enrolled = models.PositiveSmallIntegerField(default=0)
    ai_enrolled = models.PositiveSmallIntegerField(default=0)
    ai_completed = models.PositiveSmallIntegerField(default=0)
    short_enrolled = models.PositiveSmallIntegerField(default=0)
    short_completed = models.PositiveSmallIntegerField(default=0)
    specs_enrolled = models.PositiveSmallIntegerField(default=0)
    courses_enrolled = models.PositiveSmallIntegerField(default=0)
    courses_completed = models.PositiveSmallIntegerField(default=0)
    learning_hours = models.FloatField(default=0)
    last_activity = models.DateTimeField(blank=True, null=True)
    first_enrolled_at = models.DateTimeField(blank=True, null=True)
    invited_at = models.DateTimeField(blank=True, null=True)
    joined_at = models.DateTimeField(blank=True, null=True)
    days_inactive = models.PositiveIntegerField(blank=True, null=True)
    progress_band = models.CharField(max_length=40, default=C.NO_PC, db_index=True)
    fine_band = models.CharField(max_length=12, blank=True, null=True)
    # Excel-compatible (2025 workbook) helpers: specialization rows of ANY type
    spec_rows = models.PositiveSmallIntegerField(default=0)
    spec_rows_done = models.PositiveSmallIntegerField(default=0)
    best_spec_pct = models.FloatField(blank=True, null=True)
    legacy_band = models.CharField(max_length=8, blank=True, null=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class License(models.Model):
    """One Coursera membership (a row of Program Activity). Normally 1 per participant."""
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="licenses")
    account = models.CharField(max_length=120)
    email = models.EmailField()
    name = models.CharField(max_length=200)
    ext_cnic = models.CharField(max_length=13, blank=True, null=True)
    program_name = models.CharField(max_length=160, blank=True, null=True)
    program_region = models.CharField(max_length=60, blank=True, null=True)
    member_state = models.CharField(max_length=20)
    state = models.CharField(max_length=10)          # ACCEPTED / PENDING / OTHER
    removed = models.BooleanField(default=False)
    invited_at = models.DateTimeField(blank=True, null=True)
    joined_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)
    enrolled_courses = models.PositiveIntegerField(default=0)
    completed_courses = models.PositiveIntegerField(default=0)
    learning_hours = models.FloatField(default=0)
    contract = models.CharField(max_length=160, blank=True, null=True)
    match_method = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "email"], name="uniq_license_account_email")]

    def __str__(self):
        return f"{self.email} ({self.member_state})"


class SpecializationEnrollment(models.Model):
    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="spec_enrollments")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="spec_enrollments")
    specialization = models.ForeignKey(Specialization, on_delete=models.PROTECT, related_name="enrollments")
    enrolled_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)
    completed_courses = models.PositiveSmallIntegerField(default=0)
    total_courses = models.PositiveSmallIntegerField(default=0)
    progress_pct = models.FloatField(default=0)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)
    certificate_url = models.URLField(max_length=300, blank=True, null=True)
    versions = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["license", "specialization"], name="uniq_spec_per_license")]


class CourseEnrollment(models.Model):
    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="course_enrollments")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="course_enrollments")
    course_id = models.CharField(max_length=120)
    name = models.CharField(max_length=250)
    slug = models.CharField(max_length=250, blank=True, null=True)
    course_type = models.CharField(max_length=40, default="Course")
    enrolled_at = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)
    progress = models.FloatField(default=0)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(blank=True, null=True)
    grade = models.FloatField(blank=True, null=True)
    hours = models.FloatField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["license", "course_id"], name="uniq_course_per_license")]


class DataIssue(models.Model):
    SEVERITIES = [("ERROR", "Error"), ("WARNING", "Warning"), ("INFO", "Info")]
    data_import = models.ForeignKey(DataImport, on_delete=models.CASCADE, related_name="issues")
    severity = models.CharField(max_length=8, choices=SEVERITIES)
    code = models.CharField(max_length=40, db_index=True)
    message = models.TextField()
    ref = models.CharField(max_length=200, blank=True, default="")
    participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.SET_NULL, related_name="issues")

    class Meta:
        ordering = ["severity", "code", "id"]


class IdentityOverride(models.Model):
    """Manual link: 'this Coursera licence (account+email) belongs to the person with this CNIC'.
    Use it to resolve the unmatched licences listed on the Data Quality page, then re-import."""
    account = models.CharField(max_length=120, help_text="Coursera account key, e.g. gwg-ent-akepb-ii")
    email = models.EmailField(help_text="The Coursera licence email")
    cnic = models.CharField(max_length=13, help_text="13 digits, no dashes")
    note = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "email"], name="uniq_override")]

    def __str__(self):
        return f"{self.email} -> {self.cnic}"


class UserScope(models.Model):
    """Who may see what.  No scope row (and not a superuser) = no access.
    National roles see everything; otherwise the most specific of area > local council > region applies."""
    ROLES = [
        ("NATIONAL_CHAIR", "National Chairman"), ("NATIONAL_TEAM", "National Program Team"),
        ("REGIONAL_CHAIR", "Regional Chair"), ("ROM", "Regional Operating Manager"),
        ("LOCAL_CHAIR", "Local Chair"), ("PO", "Program Officer"),
    ]
    NATIONAL_ROLES = {"NATIONAL_CHAIR", "NATIONAL_TEAM"}
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gcc_scope")
    role = models.CharField(max_length=20, choices=ROLES)
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.CASCADE)
    local_council = models.ForeignKey(LocalCouncil, null=True, blank=True, on_delete=models.CASCADE)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.user} - {self.get_role_display()}"
