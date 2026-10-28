"""File readers + file-type detection (pure Python: csv + openpyxl only)."""
from __future__ import annotations

import csv
import io
import re
from datetime import date

from . import normalize as N

# Coursera enterprise export file names look like:
#   Program_Activity_gwg-ent-akepb-ii_-_CourseraEnterpriseExport_2026-09-16_05-42-47_UTC.csv
_COURSERA_NAME = re.compile(
    r"^(?P<kind>.+?)[_ ](?P<acct>gwg-ent-.+?)[_ ]-[_ ]CourseraEnterpriseExport[_ ](?P<date>\d{4}-\d{2}-\d{2})[_ ]",
    re.I,
)

KIND_BY_HEADER = [
    ("program", {"Member State", "# Enrolled Courses"}),
    ("spec", {"Specialization", "# Courses in Specialization"}),
    ("course", {"Course Slug", "Overall Progress"}),
    ("summary", {"Current Members", "Current Learners", "Total Enrollments"}),
]


def read_csv_rows(path_or_file):
    """Read a CSV into a list of dicts. Values are stripped strings; blanks stay None."""
    if hasattr(path_or_file, "read"):
        raw = path_or_file.read()
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        fh = io.StringIO(text, newline="")
    else:
        fh = open(path_or_file, newline="", encoding="utf-8-sig")
    try:
        reader = csv.DictReader(fh)
        rows = []
        for r in reader:
            rows.append({(k or "").strip(): N.clean_text(v) for k, v in r.items() if k is not None})
        return rows, [h.strip() for h in (reader.fieldnames or [])]
    finally:
        fh.close()


def identify_coursera_file(filename, headers):
    """-> (kind, account_key, as_of_date) ; kind in program|spec|course|summary|summary_dup|None."""
    base = filename.replace("\\", "/").split("/")[-1]
    m = _COURSERA_NAME.match(base)
    acct = m.group("acct").lower() if m else None
    as_of = None
    if m:
        y, mo, d = (int(x) for x in m.group("date").split("-"))
        as_of = date(y, mo, d)
    hs = set(headers)
    kind = None
    # headers decide the type; the file name is only used for the account and the date
    for k, needed in KIND_BY_HEADER:
        if needed <= hs:
            kind = k
            break
    if kind == "summary" and not any(h in hs for h in ("Business Unit",)):
        # Activity_By_City / Country exports duplicate the same totals - only the Business Unit one is used
        if m and not m.group("kind").lower().replace(" ", "_").startswith("activity_by_business_unit"):
            kind = "summary_dup"
    return kind, acct, as_of


def read_xlsx_sheets(path_or_file, max_blank_run=300):
    """-> {sheet_name: [row dict, ...]}.

    - header = first non-empty row; columns with no header are ignored
    - fully blank rows are skipped
    - reading STOPS after `max_blank_run` consecutive blank rows (some sheets declare ~1M phantom rows)
    """
    from openpyxl import load_workbook

    wb = load_workbook(path_or_file, read_only=True, data_only=True)
    out = {}
    try:
        for ws in wb.worksheets:
            header = None
            rows = []
            blank_run = 0
            for values in ws.iter_rows(values_only=True):
                if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                    blank_run += 1
                    if blank_run >= max_blank_run:
                        break
                    continue
                blank_run = 0
                if header is None:
                    header = [N.clean_text(h) for h in values]
                    continue
                row = {}
                for h, v in zip(header, values):
                    if h is None:
                        continue
                    if isinstance(v, str):
                        v = v.strip() or None
                    row[h] = v
                rows.append(row)
            out[ws.title] = rows
    finally:
        wb.close()
    return out


def looks_like_master(sheets):
    for name, rows in sheets.items():
        if rows and "Residing Region" in rows[0] and "CNIC Number" in rows[0]:
            return name
    return None


def looks_like_final_list(sheets):
    hits = {}
    for name, rows in sheets.items():
        if rows and "License Issued Category" in rows[0] and "Full Name" in rows[0]:
            hits[name] = rows
    return hits
