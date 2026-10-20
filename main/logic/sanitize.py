"""Cell / sheet-title sanitising for exports (pure Python so it is unit-tested without Django)."""
import re
from datetime import date, datetime

_PHONE_OR_NUMBER = re.compile(r"[+\-]?[\d\s().\-]+")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def cell_value(v):
    """Make a value safe for CSV / Excel. Strings that could be executed as a spreadsheet formula are neutralised
    with a leading apostrophe, EXCEPT plain phone numbers / numbers such as '+923001234567' (they cannot execute)."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        if v[:1] in ("+", "-") and _PHONE_OR_NUMBER.fullmatch(v):
            return v
        return "'" + v
    return v


def sheet_title(title):
    """Excel sheet names: max 31 chars and none of  [ ] : * ? / \\"""
    t = _BAD_SHEET_CHARS.sub("-", str(title)).strip("' ")
    return (t or "Report")[:31]
