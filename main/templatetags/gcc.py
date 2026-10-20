from django import template

from main.logic import catalog as C

register = template.Library()


@register.filter
def status_label(v):
    return C.STATUS_LABELS.get(v, v)


@register.filter
def license_label(v):
    return C.LICENSE_LABELS.get(v, v)


@register.filter
def category_label(v):
    return C.CATEGORY_LABELS.get(v, v)


@register.filter
def heat(value, maximum):
    """Background colour for heat-map cells (0 = blank)."""
    try:
        v, m = float(value), float(maximum)
    except (TypeError, ValueError):
        return ""
    if v <= 0 or m <= 0:
        return ""
    return f"background: rgba(31,111,235,{0.10 + 0.75 * (v / m):.2f}); color: {'#fff' if v / m > 0.55 else '#10243e'};"


@register.filter
def getitem(d, k):
    try:
        return d[k]
    except (KeyError, TypeError, IndexError):
        return ""


@register.filter
def fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return f"{int(f):,}" if f == int(f) else f"{f:,.1f}"
