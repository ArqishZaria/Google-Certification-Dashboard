"""Reference data & classification rules for the GCC programme (pure Python).

All "business vocabulary" lives here so it can be reviewed in one place:
regions, how a Coursera specialization is classified, how registration answers
are bucketed, progress bands, etc.
"""
from __future__ import annotations

import re
from datetime import date

# --------------------------------------------------------------------------- regions
REGION_ORDER = [
    "Southern", "Central", "Gilgit", "Hunza", "Ishkoman Puniyal",
    "Gupis Yasin", "Upper Chitral", "Lower Chitral", "National",
]
_REGION_KEYS = {r.lower(): r for r in REGION_ORDER}
_DROP_TOKENS = {"region", "regional", "council", "employee", "employees", "akepb"}

# Final-list workbook sheet name -> region
SHEET_REGION = {
    "Southern": "Southern", "Central": "Central", "Gilgit": "Gilgit", "Hunza": "Hunza",
    "IP": "Ishkoman Puniyal", "GY": "Gupis Yasin", "UC": "Upper Chitral",
    "Upper Chitral": "Upper Chitral", "LC": "Lower Chitral", "National": "National",
}


def canon_region(text):
    """'Ishkoman & Puniyal Region' / 'AKEPB - Gilgit II' / 'Southern (Karachi...)' -> canonical region name."""
    if not text:
        return None
    s = str(text).lower().replace("&", " ")
    tokens = [t for t in re.findall(r"[a-z]+", s) if t not in _DROP_TOKENS and t != "and"]
    key = " ".join(tokens)
    for k, v in _REGION_KEYS.items():
        if key == k or key.startswith(k + " "):
            return v
    return None


def region_from_program(program_name):
    """'AKEPB - Ishkoman Puniyal (Female)' -> 'Ishkoman Puniyal'. 'National Employee' -> 'National'."""
    if not program_name:
        return None
    s = re.sub(r"\((male|female)\)", "", str(program_name), flags=re.I)
    s = re.sub(r"^\s*akepb\s*-\s*", "", s, flags=re.I)
    s = re.sub(r"\b(i{1,3}|iv|v)\b\s*$", "", s.strip(), flags=re.I)  # 'Gilgit II' -> 'Gilgit'
    return canon_region(s)


def gender_from_program(program_name):
    m = re.search(r"\((male|female)\)", str(program_name or ""), flags=re.I)
    return m.group(1).title() if m else None


# --------------------------------------------------------------------------- specializations
PC, AI, SHORT = "PC", "AI", "SHORT"
CATEGORY_LABELS = {
    PC: "Professional Certificate",
    AI: "AI / Essentials course",
    SHORT: "Short course / other",
}

# slug -> (family label, category).  Admin can override category/family in the DB.
# 'family' groups localised/versioned variants of the same programme.
SPEC_CATALOG = {
    # ---- Professional Certificates (count towards GCC completion) ----
    "google-data-analytics": ("Data Analytics", PC),
    "google-advanced-data-analytics": ("Advanced Data Analytics", PC),
    "google-business-intelligence": ("Business Intelligence", PC),
    "google-cybersecurity": ("Cybersecurity", PC),
    "google-digital-marketing-ecommerce": ("Digital Marketing & E-commerce", PC),
    "certificado-de-carrera-de-google-marketing-digital-e-e-commerce": ("Digital Marketing & E-commerce", PC),
    "google-it-support": ("IT Support", PC),
    "google-it-automation": ("IT Automation with Python", PC),
    "google-project-management": ("Project Management", PC),
    "google-ux-design": ("UX Design", PC),
    "google-ai": ("Google AI", PC),  # 7/8-course "Google AI Professional Certificate" (AI Fundamentals ... AI for App Deployment)
    # ---- AI / Essentials courses (reported separately; never counted as GCC completion) ----
    "ai-essentials-google": ("AI Essentials", AI),
    "prompting-essentials-google": ("Prompting Essentials", AI),
    "google-accelerate-your-job-search-with-ai": ("Accelerate Your Job Search with AI", AI),
    # ---- Short courses ----
    "google-agile-essentials": ("Agile Essentials", SHORT),
    "google-people-management-essentials": ("People Management Essentials", SHORT),
    "google-stakeholder-management": ("Stakeholder Management", SHORT),
    "google-digital-customer-engagement": ("Digital Customer Engagement", SHORT),
    "google-data-analysis-with-python": ("Data Analysis with Python", SHORT),
    "google-network-security": ("Network Security", SHORT),
}

# fallbacks for specializations whose slug we have never seen
_NAME_FALLBACK = [
    (re.compile(r"\bessentials\b|\bprompting\b|\bwith ai\b", re.I), AI),
]


def classify_specialization(slug, name, overrides=None):
    """Return (family, category, known) for a specialization.
    `overrides` is {slug: {'family':..., 'category':...}} loaded from the DB (admin edits win)."""
    slug = (slug or "").strip().lower()
    if overrides and slug in overrides:
        o = overrides[slug]
        return o.get("family") or _tidy_name(name), o.get("category") or SHORT, True
    if slug in SPEC_CATALOG:
        fam, cat = SPEC_CATALOG[slug]
        return fam, cat, True
    # unseen specialization: never silently counted as a Professional Certificate
    fam = _tidy_name(name)
    cat = SHORT
    for rx, c in _NAME_FALLBACK:
        if rx.search(name or ""):
            cat = c
            break
    return fam, cat, False


def _tidy_name(name):
    s = re.sub(r"^\s*google\s+", "", str(name or "").strip(), flags=re.I)
    return s or "Unknown specialization"


# registration form answer -> specialization family
PREFERRED_FAMILY = {
    "digital marketing & e-commerce": "Digital Marketing & E-commerce",
    "data analysis": "Data Analytics",
    "data analytics": "Data Analytics",
    "ai essentials": "AI Essentials",
    "cybersecurity": "Cybersecurity",
    "project management": "Project Management",
    "advanced data analytics": "Advanced Data Analytics",
    "advanced business intelligence": "Business Intelligence",
    "business intelligence": "Business Intelligence",
    "it support (101)": "IT Support",
    "it support": "IT Support",
    "it automation (201)": "IT Automation with Python",
    "it automation": "IT Automation with Python",
    "ux design": "UX Design",
    "prompting essentials": "Prompting Essentials",
    "agile essentials": "Agile Essentials",
}


def preferred_family(label):
    if not label:
        return None
    return PREFERRED_FAMILY.get(str(label).strip().lower(), str(label).strip())


# --------------------------------------------------------------------------- registration buckets
INCOME_BANDS = [
    "5,000 to 15,000 PKR", "15,001 to 30,000 PKR", "30,001 to 50,000 PKR",
    "50,001 to 75,000 PKR", "75,001 to 100,000 PKR", "100,001 to 200,000 PKR",
    "200,001 to 300,000 PKR", "Above 300,000 PKR",
]
INCOME_UNKNOWN = "Data not available"

EDUCATION_GROUPS = [
    "Below Matric", "Matric", "FSc / Intermediate", "Diploma / Certification / Other",
    "Bachelors", "Masters / MPhil", "PhD", "Data not available",
]
PROF_STATUSES = ["Student", "Employed", "Self Employed", "Other Income", "Unemployed", "Data not available"]
AGE_BRACKETS = ["Under 18", "18-25", "26-35", "36-45", "46 and above", "Data not available"]


def education_group(level):
    s = (level or "").lower()
    if not s:
        return "Data not available"
    if "primary" in s or "ecd" in s or "pre-primary" in s:
        return "Below Matric"
    if "secondary (till matric" in s or "matric" in s and "higher" not in s:
        return "Matric"
    if "higher secondary" in s or "inter" in s or "a-level" in s:
        return "FSc / Intermediate"
    if "phd" in s or "doctor" in s:
        return "PhD"
    if "m. phil" in s or "mphil" in s or "master" in s or "post graduate" in s:
        return "Masters / MPhil"
    if "bachelor" in s or "graduate" in s:
        return "Bachelors"
    if "diploma" in s or "certification" in s or "other" in s:
        return "Diploma / Certification / Other"
    return "Diploma / Certification / Other"


def professional_status(currently_studying, profession, occupation_type):
    """Student > Self Employed / Employed / Other Income > Unemployed."""
    if (currently_studying or "").strip().lower() == "yes":
        return "Student"
    prof = (profession or "").strip().lower()
    occ = (occupation_type or "").strip().lower()
    if prof == "employed":
        if occ == "salaried":
            return "Employed"
        if occ.startswith("self-employment"):
            return "Self Employed"
        if occ:
            return "Other Income"
        return "Employed"
    if prof == "unemployed":
        return "Unemployed"
    return "Data not available"


def compute_age(dob, reported_age, as_of: date):
    """Age from DOB when plausible, else the reported age if plausible, else None."""
    if dob is not None:
        d = dob.date() if hasattr(dob, "date") else dob
        age = as_of.year - d.year - ((as_of.month, as_of.day) < (d.month, d.day))
        if 10 <= age <= 100:
            return age
    if reported_age is not None and 10 <= reported_age <= 100:
        return int(reported_age)
    return None


def age_bracket(age):
    if age is None:
        return "Data not available"
    if age < 18:
        return "Under 18"
    if age <= 25:
        return "18-25"
    if age <= 35:
        return "26-35"
    if age <= 45:
        return "36-45"
    return "46 and above"


# --------------------------------------------------------------------------- progress bands
# Convention everywhere: lower bound INCLUSIVE, upper bound EXCLUSIVE.
NO_PC = "No Professional Certificate enrolled"
BRD_BANDS = ["Below 20%", "20-49%", "50-74%", "75-99%", "100%"]
# Excel-compatible bands: labels and edges exactly as in the 2025 workbook (a value ON the edge belongs to the
# lower band, e.g. exactly 20% is "<20%", exactly 50% is "<50%", exactly 75% is "<75%").
FINE_BANDS = ["On Zero %", "<20%", "<30%", "<40%", "<50%", "<75%", "<85%", "<99%", "On 100%"]
_FINE_EDGES = [(20, "<20%"), (30, "<30%"), (40, "<40%"), (50, "<50%"), (75, "<75%"), (85, "<85%")]


def _clean(p):
    return None if p is None else round(float(p), 4)   # kills float noise such as 30.000000000000004


def brd_band(p):
    p = _clean(p)
    if p is None:
        return NO_PC
    if p >= 100:
        return "100%"
    if p >= 75:
        return "75-99%"
    if p >= 50:
        return "50-74%"
    if p >= 20:
        return "20-49%"
    return "Below 20%"


def fine_band(p):
    p = _clean(p)
    if p is None:
        return None
    if p >= 100:
        return "On 100%"
    if p <= 0:
        return "On Zero %"
    for edge, label in _FINE_EDGES:
        if p <= edge:
            return label
    return "<99%"


def legacy_band(best_pct, any_completed):
    """Excel 'candidate' band: each started candidate is placed ONCE, by their most advanced specialization."""
    if any_completed:
        return "100%"
    p = _clean(best_pct)
    if p is None:
        return None
    if p > 50:
        return ">50%"
    if p > 0:
        return "<50%"
    return "0%"


# --------------------------------------------------------------------------- statuses
STATUS_LABELS = {
    "COMPLETED": "Completed Professional Certificate",
    "IN_PROGRESS": "In progress (Professional Certificate)",
    "NO_PC": "Started - no Professional Certificate",
    "NOT_STARTED": "Accepted - not started",
    "PENDING": "License pending acceptance",
    "NO_LICENSE": "Shortlisted - no license found",
    "OTHER": "License removed / other",
}
STATUS_ORDER = ["COMPLETED", "IN_PROGRESS", "NO_PC", "NOT_STARTED", "PENDING", "NO_LICENSE", "OTHER"]
LICENSE_LABELS = {"ACCEPTED": "Accepted", "PENDING": "Pending", "NONE": "No license", "REMOVED": "Removed", "OTHER": "Other"}
