"""Pure-Python normalisation helpers (no Django imports, so they are unit-testable anywhere).

Every identifier that is used as a join key goes through one of these functions, so the
same person is always represented the same way regardless of which export they came from.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

_WS = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")


def clean_text(v):
    """Trim, collapse whitespace, turn empty/NaN into None."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    s = _WS.sub(" ", str(v).replace("\u00a0", " ")).strip()
    return s or None


def norm_email(v):
    s = clean_text(v)
    if not s:
        return None
    s = s.replace(" ", "").lower()
    return s if "@" in s and not s.startswith("@") else None


def norm_cnic(v):
    """Return the 13-digit CNIC as a string, or None if it is missing/invalid.

    Handles ints, floats (Excel stores long numbers as floats), and formatted
    strings such as '42101-1234567-1'. A CNIC is NEVER stored as a number.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        s = str(v)
    elif isinstance(v, float):
        if v != v:
            return None
        s = str(int(v)) if v.is_integer() else str(v)
    else:
        s = str(v).strip()
    digits = _NON_DIGIT.sub("", s)
    return digits if len(digits) == 13 else None


def phone_digits(v):
    s = clean_text(v)
    return _NON_DIGIT.sub("", s) if s else ""


def parse_dt(v):
    """Parse ISO strings / datetimes / dates into timezone-aware UTC datetimes.
    Returns None for blanks and for impossible dates (year < 1900, e.g. typo'd DOBs)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    else:
        s = str(v).strip()
        if not s or s.lower() in ("nan", "nat", "none", "null"):
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = None
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.year < 1900:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_float(v, default=None):
    s = clean_text(v)
    if s is None:
        return default
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return default


def parse_int(v, default=None):
    f = parse_float(v)
    if f is None:
        return default
    return int(f)


def is_yes(v):
    s = clean_text(v)
    return bool(s) and s.lower() in ("yes", "true", "y", "1")


def name_key(v):
    """Loose key used ONLY to suggest possible matches to a human reviewer (never to merge)."""
    s = clean_text(v) or ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def stable_id(key: str) -> str:
    """Short, stable, non-guessable public id for URLs (so we never put a CNIC in a URL)."""
    return hashlib.sha1(("gcc|" + key).encode("utf-8")).hexdigest()[:12]


def title_gender(v):
    s = clean_text(v)
    if not s:
        return None
    s = s.lower()
    if s.startswith("m"):
        return "Male"
    if s.startswith("f"):
        return "Female"
    return None


def getcol(row: dict, *prefixes):
    """Fetch a column by exact name or by case-insensitive prefix (headers vary slightly between sheets)."""
    for p in prefixes:
        if p in row and clean_text(row[p]) is not None:
            return row[p]
    low = {str(k).lower(): k for k in row}
    for p in prefixes:
        pl = p.lower()
        for lk, k in low.items():
            if lk.startswith(pl) and clean_text(row[k]) is not None:
                return row[k]
    return None
