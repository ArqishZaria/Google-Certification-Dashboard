"""Turn a bag of uploaded/located files into pipeline Sources (pure Python)."""
from __future__ import annotations

import os
from datetime import date

from . import metrics as M
from . import pipeline as P
from . import readers as R


class MissingFilesError(ValueError):
    """Required inputs are not (all) present yet - not a data error, just an incomplete file set."""


def classify_path(path):
    """Detect what one file is, from its CONTENT. -> dict(name, kind, account, file_date, description).
    kind: master | final | program | spec | course | summary | ignored"""
    name = os.path.basename(path)
    low = name.lower()
    out = {"name": name, "kind": "ignored", "account": "", "file_date": None, "description": "Not used by the dashboard"}
    if low.endswith((".xlsx", ".xlsm")):
        sheets = R.read_xlsx_sheets(path)
        m, f = R.looks_like_master(sheets), R.looks_like_final_list(sheets)
        if m and not f:
            out.update(kind="master", description=f"Registration master ({len(sheets[m])} rows)")
        elif f and not m:
            out.update(kind="final", description=f"Shortlist / final list ({sum(len(v) for v in f.values())} rows in {len(f)} sheets)")
        else:
            out["description"] = "Not a registration master or final-list workbook"
    elif low.endswith(".csv"):
        rows, headers = R.read_csv_rows(path)
        kind, acct, fdate = R.identify_coursera_file(name, headers)
        if kind in ("program", "spec", "course") and acct:
            label = {"program": "Program", "spec": "Specialisation", "course": "Course"}[kind]
            out.update(kind=kind, account=acct, file_date=fdate, description=f"Coursera {label} Activity - {len(rows)} rows")
        elif kind == "summary" and acct and rows:
            out.update(kind="summary", account=acct, file_date=fdate, description="Coursera control totals (Activity By Business Unit)")
        else:
            out["description"] = "Coursera file not used by the dashboard"
    else:
        out["description"] = "Unsupported file type"
    return out


def load_sources(paths, overrides=None, spec_overrides=None, inactivity_days=M.DEFAULT_INACTIVITY_DAYS, as_of=None):
    """`paths`: list of file paths (xlsx + csv, any order). File type is detected from CONTENT, not just the name.
    Returns (Sources, detected) where detected is a list of (filename, description)."""
    master_rows, final_sheets = None, None
    coursera, dates, detected = {}, [], []
    for path in paths:
        name = os.path.basename(path)
        low = name.lower()
        if low.endswith((".xlsx", ".xlsm")):
            sheets = R.read_xlsx_sheets(path)
            m = R.looks_like_master(sheets)
            f = R.looks_like_final_list(sheets)
            if m and not f:
                master_rows = sheets[m]
                detected.append((name, f"Registration master ({len(master_rows)} rows)"))
            elif f and not m:
                final_sheets = f
                detected.append((name, f"Shortlist / final list ({sum(len(v) for v in f.values())} rows in {len(f)} sheets)"))
            else:
                detected.append((name, "IGNORED - not a registration master or final-list workbook"))
        elif low.endswith(".csv"):
            rows, headers = R.read_csv_rows(path)
            kind, acct, file_date = R.identify_coursera_file(name, headers)
            if kind in ("program", "spec", "course") and acct:
                if kind in coursera.get(acct, {}):
                    raise ValueError(f"Two Coursera '{kind}' files were supplied for account {acct} ({name}). "
                                     "Upload exactly one export of each type per Coursera account.")
                coursera.setdefault(acct, {})[kind] = rows
                detected.append((name, f"Coursera {kind} activity - account {acct} ({len(rows)} rows)"))
                if file_date:
                    dates.append(file_date)
            elif kind == "summary" and acct:
                if rows and "summary" in coursera.get(acct, {}):
                    raise ValueError(f"Two Coursera summary (Activity By Business Unit) files were supplied for account {acct} ({name}).")
                if rows:
                    coursera.setdefault(acct, {})["summary"] = rows[0]
                detected.append((name, f"Coursera summary control totals - account {acct}"))
            else:
                detected.append((name, "Ignored (not used by the dashboard)"))
        else:
            detected.append((name, "IGNORED - unsupported file type"))

    missing = []
    if master_rows is None:
        missing.append("registration master workbook (.xlsx with 'Residing Region' and 'CNIC Number')")
    if final_sheets is None:
        missing.append("final-list workbook (.xlsx with 'License Issued Category')")
    if not any("program" in d for d in coursera.values()):
        missing.append("Coursera Program Activity CSV")
    if missing:
        raise MissingFilesError("Missing required file(s): " + "; ".join(missing))
    for acct, d in coursera.items():
        d.setdefault("program", [])
        d.setdefault("spec", [])
        d.setdefault("course", [])
        d.setdefault("summary", None)
    snapshot = as_of or (max(dates) if dates else date.today())
    src = P.Sources(master_rows=master_rows, final_sheets=final_sheets, coursera=coursera, as_of=snapshot,
                    overrides=overrides or {}, spec_overrides=spec_overrides or {}, inactivity_days=inactivity_days)
    return src, detected
