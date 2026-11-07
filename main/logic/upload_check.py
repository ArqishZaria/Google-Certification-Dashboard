"""Friendly pre-flight checks for the three upload boxes (pure Python, no Django).

Every function returns a dict: {"ok": bool, "errors": [...], "warnings": [...], "info": [...]}
Messages are written for non-technical users: what is wrong, which box it belongs in, what to do.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from collections import defaultdict

from . import catalog as C
from . import readers as R
from . import templates_gen as T

MAX_ZIP_FILES = 200
MAX_UNZIPPED_BYTES = 600 * 1024 * 1024


class UploadError(ValueError):
    """A problem with an uploaded file that the user can fix."""


def _result(errors=None, warnings=None, info=None, **extra):
    errors, warnings, info = errors or [], warnings or [], info or []
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info, **extra}


def _norm(h):
    return (h or "").strip().lower()


def _missing(headers, required):
    have = {_norm(h) for h in headers}
    return [r for r in required if _norm(r) not in have]


# ------------------------------------------------------------------ zip handling
def safe_extract_zip(zip_path, dest_dir, label=None):
    """Extract only the .csv files of a zip into dest_dir. Never trusts paths inside the zip (zip-slip safe),
    skips folders / hidden / __MACOSX entries, refuses zip bombs. -> (csv_paths, notes)"""
    label = label or os.path.basename(zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise UploadError(f"'{label}' is not a valid ZIP file. Please upload the ZIP exactly as it was downloaded from Coursera.")
    os.makedirs(dest_dir, exist_ok=True)
    csvs, notes, total, n = [], [], 0, 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            parts = [p for p in name.replace("\\", "/").split("/") if p]
            if info.is_dir() or not parts or parts[0] == "__MACOSX" or parts[-1].startswith("."):
                continue
            base = parts[-1]
            if not base.lower().endswith(".csv"):
                notes.append(f"'{base}' inside {label} was ignored (not a CSV file).")
                continue
            n += 1
            if n > MAX_ZIP_FILES:
                raise UploadError(f"'{label}' contains too many files. Please upload the original Coursera export.")
            target_dir = os.path.join(dest_dir, f"{n:03d}")
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, base)          # only the base name is used: no path traversal possible
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UNZIPPED_BYTES:
                        raise UploadError(f"'{label}' is unexpectedly large when unzipped. Please upload the original Coursera export.")
                    out.write(chunk)
            csvs.append(target)
    if not csvs:
        raise UploadError(f"'{label}' does not contain any CSV files. Please upload the ZIP exactly as downloaded from Coursera.")
    return csvs, notes


# ------------------------------------------------------------------ Step 1: master sheet
def check_master(path):
    name = os.path.basename(path)
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return _result(errors=[f"Step 1 needs an Excel file (.xlsx). '{name}' is not one."])
    try:
        headers = R.read_xlsx_headers(path)
    except Exception:
        return _result(errors=[f"'{name}' could not be opened as an Excel file. Open it in Excel and save it again as .xlsx."])
    best = None
    for sheet, hdr in headers.items():
        if not _missing(hdr, ["Residing Region", "CNIC Number"]):
            best = (sheet, hdr)
            break
    if best is None:
        if any(not _missing(h, ["License Issued Category", "Full Name"]) for h in headers.values()):
            return _result(errors=["This looks like the FINAL LIST, not the Master registration sheet. Please upload it in Step 2 instead."])
        cand = max(headers.items(), key=lambda kv: len(set(map(_norm, kv[1])) & set(map(_norm, T.MASTER_HEADERS))), default=(None, []))[1]
        miss = _missing(cand, T.MASTER_REQUIRED)
        return _result(errors=["This does not look like the Master registration sheet. "
                               f"It is missing these required columns: {', '.join(miss) if miss else 'Residing Region, CNIC Number'}. "
                               "Download the template (button in Step 1) to see the exact layout."])
    sheet, hdr = best
    errors, warnings = [], []
    miss = _missing(hdr, T.MASTER_REQUIRED)
    if miss:
        errors.append(f"The Master sheet is missing required column(s): {', '.join(miss)}.")
    rows = R.read_xlsx_sheets(path).get(sheet, [])
    if not rows:
        errors.append(f"The sheet '{sheet}' has headers but no registrations yet. Paste the form responses below the header row.")
        return _result(errors=errors, warnings=warnings)
    no_cnic = sum(1 for r in rows if r.get("CNIC Number") in (None, ""))
    if no_cnic:
        warnings.append(f"{no_cnic} registration(s) have no CNIC. They will be listed on the Data quality page.")
    return _result(errors=errors, warnings=warnings, info=[f"Master sheet: {len(rows):,} registrations found in sheet '{sheet}'."], rows=len(rows))


# ------------------------------------------------------------------ Step 2: final list
def check_final(path):
    name = os.path.basename(path)
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return _result(errors=[f"Step 2 needs an Excel file (.xlsx). '{name}' is not one."])
    try:
        headers = R.read_xlsx_headers(path)
    except Exception:
        return _result(errors=[f"'{name}' could not be opened as an Excel file. Open it in Excel and save it again as .xlsx."])
    final_sheets = {s: h for s, h in headers.items() if not _missing(h, ["License Issued Category", "Full Name"])}
    if not final_sheets:
        if any(not _missing(h, ["Residing Region", "CNIC Number"]) for h in headers.values()):
            return _result(errors=["This looks like the MASTER registration sheet, not the Final list. Please upload it in Step 1 instead."])
        return _result(errors=["This does not look like the Final list. Each region's tab must have the columns "
                               f"{', '.join(T.FINAL_REQUIRED)}. Download the template (button in Step 2) to see the exact layout."])
    errors, warnings, info, empty_tabs = [], [], [], []
    data = R.read_xlsx_sheets(path)
    usable, total = 0, 0
    for sheet, hdr in final_sheets.items():
        key = sheet.strip()
        if key not in C.SHEET_REGION:
            warnings.append(f"Tab '{sheet.strip()}' is not a known region tab and will be SKIPPED. Expected tab names: {', '.join(n for n, _ in T.FINAL_SHEETS)}.")
            continue
        miss = _missing(hdr, T.FINAL_REQUIRED)
        if miss:
            errors.append(f"Tab '{sheet}' is missing required column(s): {', '.join(miss)}.")
            continue
        rows = [r for r in data.get(sheet, []) if (r.get("Full Name") or "") != ""]
        if not rows:
            empty_tabs.append(sheet.strip())
            continue
        usable += 1
        total += len(rows)
        no_cnic = sum(1 for r in rows if r.get("CNIC Number") in (None, ""))
        info.append(f"Tab '{key}' ({C.SHEET_REGION[key]}): {len(rows):,} candidates" + (f", {no_cnic} without CNIC" if no_cnic else ""))
    if not errors and usable == 0:
        errors.append("The Final list has headers but no candidates yet. Paste each region's shortlisted people below the header row of the matching tab.")
    elif empty_tabs:
        info.append("Empty tabs (nothing to import): " + ", ".join(empty_tabs))
    return _result(errors=errors, warnings=warnings, info=info, rows=total)


# ------------------------------------------------------------------ Step 3: Coursera
def check_coursera(csv_paths, extra_notes=None):
    """csv_paths: all CSV files (already extracted from zips)."""
    errors, warnings, info = [], [], list(extra_notes or [])
    by_acct = defaultdict(dict)
    dates, ignored = [], []
    for p in csv_paths:
        name = os.path.basename(p)
        try:
            rows, headers = R.read_csv_rows(p)
        except Exception:
            errors.append(f"'{name}' could not be read as a CSV file.")
            continue
        kind, acct, d = R.identify_coursera_file(name, headers)
        if kind in ("program", "spec", "course", "summary") and not acct:
            errors.append(f"'{name}' looks like a Coursera export but its file name was changed, so we cannot tell which Coursera account it belongs to. "
                          "Please do not rename the files - upload the original ZIP.")
            continue
        if kind in ("program", "spec", "course", "summary") and acct:
            if kind in by_acct[acct]:
                errors.append(f"Two '{kind}' files were found for Coursera account {acct}. Upload only one export per account.")
                continue
            by_acct[acct][kind] = (name, len(rows))
            if d and kind != "summary":
                dates.append(d)
        else:
            ignored.append(name)
    LABEL = {"program": "Program Activity", "spec": "Specialisation Activity", "course": "Course Activity", "summary": "Activity By Business Unit"}
    if not by_acct and not errors:
        errors.append("No Coursera 'Program Activity' file was found. Upload the ZIP(s) downloaded from Coursera.")
    accounts = {}
    for acct, kinds in sorted(by_acct.items()):
        if "program" not in kinds:
            errors.append(f"Coursera account {acct}: the 'Program Activity' file is missing.")
        elif kinds["program"][1] == 0:
            errors.append(f"Coursera account {acct}: the 'Program Activity' file has no rows (it looks like an empty or template file).")
        if "spec" not in kinds:
            errors.append(f"Coursera account {acct}: the 'Specialisation Activity' file is missing. Without it everybody would show as 'not started'.")
        if "course" not in kinds:
            warnings.append(f"Coursera account {acct}: no 'Course Activity' file - individual-course progress will not be available.")
        if "summary" not in kinds:
            warnings.append(f"Coursera account {acct}: no 'Activity By Business Unit' file - we cannot double-check our numbers against Coursera's totals.")
        accounts[acct] = {k: kinds[k] for k in kinds}
        parts = ", ".join(f"{LABEL[k]} {'OK' if k in kinds else 'MISSING'}" for k in ("program", "spec", "course", "summary"))
        n = kinds.get("program", (None, 0))[1]
        info.append(f"Coursera account {acct}: {n:,} licences ({parts})")
    if len(set(dates)) > 1:
        warnings.append("The Coursera files have different export dates (" + ", ".join(sorted({d.isoformat() for d in dates})) + "). Download all exports on the same day.")
    elif dates:
        info.append(f"Coursera data date: {dates[0]:%d %b %Y}")
    if ignored:
        info.append(f"{len(ignored)} other Coursera file(s) were ignored (not needed): " + ", ".join(sorted(ignored)[:6]) + ("..." if len(ignored) > 6 else ""))
    return _result(errors=errors, warnings=warnings, info=info, accounts=accounts)


# ------------------------------------------------------------------ the whole upload flow (used by the view)
def save_upload(f, folder):
    """Write an uploaded file (any object with .name and .chunks()) under `folder` using only its base name."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, os.path.basename(f.name))
    with open(path, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)
    return path


def prepare_uploads(master_file, final_file, coursera_files, tmp):
    """Save, unzip and validate everything the user uploaded. Nothing is imported here.
    -> {"master": path|None, "final": path|None, "coursera": [csv paths], "errors": [...], "warnings": [...], "notes": [...]}"""
    errors, warnings, notes, coursera = [], [], [], []
    master = save_upload(master_file, os.path.join(tmp, "master")) if master_file else None
    final = save_upload(final_file, os.path.join(tmp, "final")) if final_file else None
    for i, f in enumerate(coursera_files or []):
        path = save_upload(f, os.path.join(tmp, "coursera", str(i)))
        low = f.name.lower()
        if low.endswith(".zip"):
            try:
                csvs, n = safe_extract_zip(path, os.path.join(tmp, "coursera_unzipped", str(i)), f.name)
                coursera += csvs
                notes += n
            except UploadError as e:
                errors.append(f"Step 3 - Coursera export: {e}")
        elif low.endswith(".csv"):
            coursera.append(path)
        else:
            errors.append(f"Step 3 - Coursera export: '{f.name}' is not a ZIP or CSV file.")
    if not (master or final or coursera) and not errors:
        errors.append("Please choose at least one file to upload.")
    for label, path, fn in (("Step 1 - Master sheet", master, check_master), ("Step 2 - Final list", final, check_final)):
        if path:
            r = fn(path)
            errors += [f"{label}: {m}" for m in r["errors"]]
            warnings += [f"{label}: {m}" for m in r["warnings"]]
    if coursera:
        r = check_coursera(coursera, notes)
        errors += [f"Step 3 - Coursera export: {m}" for m in r["errors"]]
        warnings += [f"Step 3 - Coursera export: {m}" for m in r["warnings"]]
    return {"master": master, "final": final, "coursera": coursera, "errors": errors, "warnings": warnings, "notes": notes}
