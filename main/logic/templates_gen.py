"""Downloadable upload templates (pure Python: openpyxl + zipfile, no Django).

  master_template_bytes()       -> .xlsx  "Registration master sheet" with the exact columns of the Google Form export
  final_template_bytes()        -> .xlsx  "Final list" with one sheet per region
  coursera_template_zip_bytes() -> .zip   guide + header-only CSVs showing the Coursera export layout

Templates contain HEADERS ONLY in the data sheets (no example rows), so a template that is uploaded by mistake can
never inject fake people. Examples live on the "READ ME FIRST" sheet.
"""
from __future__ import annotations

import csv
import io
import zipfile

from . import catalog as C

MASTER_SHEET = "Certificate Applications"

# Exact column order of the real Google-Form export. (required?, what to put, example)
MASTER_COLUMNS = [
    ("id", True, "Unique number of each form submission. Must not repeat.", "3512"),
    ("Full Name", True, "Applicant's full name.", "Ayesha Khan"),
    ("Email Address", False, "Personal email address.", "ayesha@gmail.com"),
    ("Email Address (Course Access)", True, "The email the person uses to log in to Coursera. This is the main key that links people to their Coursera licence.", "ayesha@gmail.com"),
    ("Mobile Number", False, "Phone number (used for follow-up lists).", "+923001234567"),
    ("CNIC Number", True, "13 digits. Dashes are fine (42101-1234567-1).", "4210112345671"),
    ("Gender", False, "Male or Female.", "Female"),
    ("Marital Status", False, "", "Single"),
    ("Date of Birth", False, "A real date.", "2001-05-17"),
    ("Age (completed years)", False, "Whole number of years.", "25"),
    ("What is your place of origin?", False, "", "Hunza"),
    ("Currently Studying", False, "Yes or No.", "No"),
    ("Education Level", False, "As in the form (e.g. 'Bachelors (Graduate)').", "Bachelors (Graduate)"),
    ("Profession", False, "Employed or Unemployed.", "Unemployed"),
    ("currently_earning", False, "Not used by the dashboard.", ""),
    ("occupation_type", False, "e.g. Salaried, Self-employment...", "Salaried"),
    ("no_earning_reason", False, "Not used by the dashboard.", ""),
    ("I have access to a stable internet connection", False, "Not used by the dashboard.", ""),
    ("I have an electricity back-up at home", False, "Not used by the dashboard.", ""),
    ("I have access to a personal laptop/computer", False, "Not used by the dashboard.", ""),
    ("Total family members", False, "Not used by the dashboard.", ""),
    ("Number of earning family members", False, "Not used by the dashboard.", ""),
    ("Monthly family income", False, "Exactly as the form band, e.g. '30,001 to 50,000 PKR'.", "30,001 to 50,000 PKR"),
    ("Writing", False, "Not used by the dashboard.", ""),
    ("Reading", False, "Not used by the dashboard.", ""),
    ("Speaking", False, "Not used by the dashboard.", ""),
    ("Residing Region", True, "Where the person lives. Choose from the list: Southern Region, Central Region, Gilgit Region, Hunza Region, Ishkoman & Puniyal Region, Gupis & Yasin Region, Upper Chitral Region, Lower Chitral Region.", "Southern Region"),
    ("Current Local Council", False, "Local council the person belongs to.", "Garden"),
    ("JK Attendance", False, "Jamatkhana the person attends (this is the 'Area').", "Amynabad"),
    ("Which certification would you like to enrol in?", False, "The certificate the person asked for.", "Cybersecurity"),
    ("Timeline Plan", False, "Not used by the dashboard.", ""),
    ("Programme Impact", False, "Not used by the dashboard.", ""),
    ("How did you hear about the Google Career Certificates programme?", False, "Not used by the dashboard.", ""),
    ("Have you been part of the Google Career Certificate previously?", False, "Not used by the dashboard.", ""),
    ("gcc_prior_participation_year", False, "Not used by the dashboard.", ""),
    ("Internship Interest", False, "Not used by the dashboard.", ""),
    ("Future Opportunity", False, "Not used by the dashboard.", ""),
    ("Please specify (if you selected Other).", False, "Not used by the dashboard.", ""),
    ("Suggestions & Support", False, "Not used by the dashboard.", ""),
    ("Terms Accepted", False, "Not used by the dashboard.", ""),
    ("Consent", False, "Not used by the dashboard.", ""),
    ("Created At", False, "When the form was submitted (used for the monthly growth chart).", "2026-06-01 10:00:00"),
]
MASTER_HEADERS = [c[0] for c in MASTER_COLUMNS]
MASTER_REQUIRED = [c[0] for c in MASTER_COLUMNS if c[1]]

FINAL_COLUMNS = [
    ("SN", False, "Serial number. Not used (people are matched by CNIC and email, never by this number).", "1"),
    ("License Issued Category", True, "The region/category the licence was issued under.", "Southern"),
    ("Full Name", True, "Candidate's full name.", "Ayesha Khan"),
    ("Email Address (Course Access)", True, "The email the Coursera licence was sent to.", "ayesha@gmail.com"),
    ("CNIC Number", True, "13 digits. Dashes are fine.", "4210112345671"),
    ("Gender", False, "Male or Female.", "Female"),
    ("Which certification would you like to enrol in?", False, "The certificate the person asked for.", "Cybersecurity"),
]
FINAL_HEADERS = [c[0] for c in FINAL_COLUMNS]
FINAL_REQUIRED = [c[0] for c in FINAL_COLUMNS if c[1]]
# sheet name (what the client must call the tab) -> region
FINAL_SHEETS = [("Southern", "Southern"), ("Central", "Central"), ("Gilgit", "Gilgit"), ("Hunza", "Hunza"),
                ("IP", "Ishkoman Puniyal"), ("GY", "Gupis Yasin"), ("Upper Chitral", "Upper Chitral"),
                ("LC", "Lower Chitral"), ("National", "National (council staff)")]

# Real Coursera enterprise export layouts (header rows only)
COURSERA_HEADERS = {
    "Program_Activity": ["Name", "Email", "External ID", "Program Name", "Program Slug", "# Enrolled Courses", "# Completed Courses", "Member State", "Join Date", "Invitation Date", "Last Program Activity Date", "Contract(s)", "Contract ID(s)", "Learning Hours", "Manager Name", "Manager Email", "Job Title", "Job Type", "Business Unit", "Business Unit 2", "Location City", "Location Region", "Location Country"],
    "Specialisation_Activity": ["Name", "Email", "External ID", "Specialization", "Specialization Slug", "University", "Enrollment Time", "Last Specialization Activity Time", "# Completed Courses", "# Courses in Specialization", "Completed", "Removed From Program", "Program Slug", "Program Name", "Collection Name", "Collection ID", "Specialization Completion Time", "Specialization Certificate URL", "Contract ID", "Is Enterprise Contract Active", "Total Estimated Learning Hours (since enrolled)", "Manager Name", "Manager Email", "Job Title", "Job Type", "Business Unit", "Business Unit 2", "Location City", "Location Region", "Location Country"],
    "Course_Activity": ["Name", "Email", "External ID", "Course", "Course ID", "Course Slug", "University", "Enrollment Time", "Class Start Time", "Class End Time", "Last Course Activity Time", "Overall Progress", "Completed", "Removed From Program", "Program Slug", "Program Name", "Collection Name", "Collection ID", "Completion Time", "Course Grade", "Course Certificate URL", "Contract", "Is Enterprise Contract Active", "Learning Hours", "Course Type", "Manager Name", "Manager Email", "Job Title", "Job Type", "Business Unit", "Business Unit 2", "Location City", "Location Region", "Location Country"],
    "Activity_By_Business_Unit": ["Business Unit", "Current Members", "Deleted Members", "Current Learners", "Total Enrollments", "Average Progress", "Total Completed Courses", "Total Estimated Learning Hours", "Average Estimated Learning Hours"],
}

_DROPDOWNS = {
    "Gender": '"Male,Female"',
    "Currently Studying": '"Yes,No"',
    "Profession": '"Employed,Unemployed"',
    "Residing Region": '"Southern Region,Central Region,Gilgit Region,Hunza Region,Ishkoman & Puniyal Region,Gupis & Yasin Region,Upper Chitral Region,Lower Chitral Region"',
}


def _style_header(ws, headers, required):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="B54708" if h in required else "1F3A5F")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = min(max(len(h) + 4, 14), 38)
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "A2"


def _readme(wb, title, intro_lines, table_rows, table_head):
    from openpyxl.styles import Alignment, Font
    ws = wb.active
    ws.title = "READ ME FIRST"
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    r = 3
    for line in intro_lines:
        ws.cell(row=r, column=1, value=line).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 32 if len(line) > 90 else 18
        r += 1
    r += 1
    for j, h in enumerate(table_head, 1):
        ws.cell(row=r, column=j, value=h).font = Font(bold=True)
    for row in table_rows:
        r += 1
        for j, v in enumerate(row, 1):
            ws.cell(row=r, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABCD", (44, 12, 70, 28)):
        ws.column_dimensions[col].width = w
    return ws


def master_template_bytes():
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    _readme(wb, "Registration MASTER sheet - template", [
        "WHAT THIS IS: the list of EVERYONE who filled in the Google Career Certificates registration form.",
        "HOW TO USE: export the Google Form responses to Excel, then paste the rows into the sheet 'Certificate Applications' (keep row 1, the headers, exactly as they are).",
        "The 4 orange headers are REQUIRED. Every other column is optional - extra columns are simply ignored.",
        "Do not rename the headers and do not rename the sheet tab. One row per form submission. Do not leave this file with example data in it.",
        "Then upload it on the dashboard: Import data -> Step 1.",
    ], [(h, "REQUIRED" if req else "optional", note, ex) for h, req, note, ex in MASTER_COLUMNS], ["Column", "Needed?", "What to put", "Example"])
    ws = wb.create_sheet(MASTER_SHEET)
    _style_header(ws, MASTER_HEADERS, set(MASTER_REQUIRED))
    for h, formula in _DROPDOWNS.items():
        col = MASTER_HEADERS.index(h) + 1
        dv = DataValidation(type="list", formula1=formula, allow_blank=True, errorStyle="warning")
        ws.add_data_validation(dv)
        letter = ws.cell(row=2, column=col).column_letter
        dv.add(f"{letter}2:{letter}5000")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def final_template_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    _readme(wb, "FINAL LIST (shortlisted candidates) - template", [
        "WHAT THIS IS: the candidates your regional teams shortlisted and issued a licence to - one tab per region.",
        "HOW TO USE: paste each region's shortlisted people into the tab with the region's name (keep row 1, the headers).",
        "The 4 orange headers are REQUIRED on every tab you use. Tabs you do not use can stay empty or be deleted. Extra columns are ignored.",
        "The TAB NAMES matter - use exactly the names in the table below. A tab with any other name is skipped (the page will tell you).",
        "People are matched by CNIC and email. The 'SN' / 'id' number is never used, so it does not need to be correct.",
        "Then upload it on the dashboard: Import data -> Step 2.",
    ], [(name, "tab name", f"Region: {region}", "") for name, region in FINAL_SHEETS], ["Tab name", "", "Becomes region", ""])
    for name, _ in FINAL_SHEETS:
        ws = wb.create_sheet(name)
        _style_header(ws, FINAL_HEADERS, set(FINAL_REQUIRED))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


COURSERA_README = """COURSERA EXPORT - what to upload (Step 3)
==========================================

You do NOT fill anything in. You download the export from Coursera and upload it as it is.

* Your organisation has TWO Coursera accounts. Download the export for EACH account.
  Each download is a ZIP file. Upload BOTH zips (you can pick both at once).
* Do not unzip, do not rename, do not edit the files.
* Inside each ZIP these files are used (others are ignored safely):

    Program_Activity_<account>_-_CourseraEnterpriseExport_<date>_UTC.csv         REQUIRED  (who has a licence, accepted or pending)
    Specialisation_Activity_<account>_-_CourseraEnterpriseExport_<date>_UTC.csv  REQUIRED  (progress in each certificate)
    Course_Activity_<account>_-_CourseraEnterpriseExport_<date>_UTC.csv          recommended (individual courses)
    Activity_By_Business_Unit_<account>_-_CourseraEnterpriseExport_<date>_UTC.csv recommended (Coursera's own totals; used to double-check our numbers)

* The other files in the zip (Activity_By_City, Activity_By_Country, Learning_Path_Activity, Video_Clip_Activity) are ignored.
* The header-only CSV files next to this guide just show the column layout. Do NOT upload them.
"""


def coursera_template_zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("READ ME FIRST.txt", COURSERA_README)
        for kind, headers in COURSERA_HEADERS.items():
            out = io.StringIO()
            csv.writer(out).writerow(headers)
            z.writestr(f"EXAMPLE_LAYOUT_{kind}_gwg-ent-YOUR-ACCOUNT_-_CourseraEnterpriseExport_2026-01-01_00-00-00_UTC.csv", out.getvalue())
    return buf.getvalue()


TEMPLATES = {
    "master": ("GCC_Master_Registration_TEMPLATE.xlsx", master_template_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "final": ("GCC_Final_List_TEMPLATE.xlsx", final_template_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "coursera": ("GCC_Coursera_Upload_Guide.zip", coursera_template_zip_bytes, "application/zip"),
}
