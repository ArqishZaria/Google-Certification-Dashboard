"""Logic behind the 'Resolve queries' page: build the queues of unresolved cases, suggest matches, apply decisions.
Decisions are stored (IdentityOverride / SeatChange / Dismissal) and re-applied on every import, so they survive re-uploads."""
import re
from difflib import SequenceMatcher

from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .logic import catalog as C
from .logic import normalize as N
from .models import DataImport, DataIssue, Dismissal, IdentityOverride, License, Participant, SeatChange

SEAT_CODE, LIC_CODE = "SHORTLISTED_NO_LICENSE", "LICENSE_NOT_SHORTLISTED"
OWN_SECTIONS = {SEAT_CODE, LIC_CODE, "LICENSE_UNIDENTIFIED", "UNCLASSIFIED_SPECIALIZATION"}
CODE_LABELS = {
    "FINAL_MULTI_EMAIL": "Same person listed with two emails", "FINAL_DUP_ROW": "Same row listed twice",
    "FINAL_BAD_CNIC": "Final list: missing or invalid CNIC", "SHORTLIST_NO_REGISTRATION": "On the Final list but no registration found",
    "CNIC_MISMATCH_REG": "CNIC differs from the registration", "REGISTRATION_SHARED": "One registration matches two people",
    "REG_BAD_AGE": "Registration: age looks wrong", "RESIDENCE_ALLOCATION_MISMATCH": "Lives in a different region from the Final list",
    "REGION_MISMATCH": "Final list region differs from the Coursera programme", "LICENSE_CNIC_CONFLICT": "Coursera ID differs from the Final-list CNIC",
    "COMPLETED_PARTIAL": "Completed but not every course is done", "COMPLETED_BEFORE_ENROLMENT": "Completed before enrolling",
    "ADDED_SEAT_NO_CNIC": "Seat added by hand without a CNIC", "SEAT_REMOVED": "Seat removed by a decision",
    "SHORTLISTED_NO_LICENSE": "Seat without a licence", "LICENSE_NOT_SHORTLISTED": "Licence not on the Final list",
    "POSSIBLE_MISSING_EXPORT": "A Coursera export may be missing", "LICENSE_UNIDENTIFIED": "Licence that matches nobody",
    "MULTIPLE_LICENSES": "Person holds more than one licence", "FINAL_EMAIL_CONFLICT": "One email used by two people",
}


def _n(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def similarity(name_a, email_a, name_b, email_b):
    """(best score 0-1, name score, email-local-part score). Used only to SUGGEST; a person always confirms."""
    sn = SequenceMatcher(None, _n(name_a), _n(name_b)).ratio()
    se = SequenceMatcher(None, _n((email_a or "").split("@")[0]), _n((email_b or "").split("@")[0])).ratio()
    return max(sn, se), sn, se


def _lic_cnic(lic):
    return lic.ext_cnic or lic.participant.cnic


def _pairing(seat, lic):
    best, sn, se = similarity(seat.name, seat.email, lic.name, lic.email)
    lc = _lic_cnic(lic)
    return {"lic": lic, "seat": seat, "score": best, "name_pct": round(sn * 100), "email_pct": round(se * 100),
            "cnic_conflict": bool(seat.cnic and lc and seat.cnic != lc),
            "region_differs": bool(lic.program_region and seat.region and lic.program_region != seat.region.name)}


def build_context(imp):
    since = imp.created_at
    over, changes, dismissals = list(IdentityOverride.objects.all()), list(SeatChange.objects.all()), list(Dismissal.objects.all())
    dismissed = {(d.code, d.ref) for d in dismissals}
    pend_over = [o for o in over if o.created_at > since]
    pend_changes = [c for c in changes if c.created_at > since]
    pend_pairs = {(o.account, o.email.lower()) for o in pend_over}
    pend_cnic = {o.cnic for o in pend_over}
    pend_seat_emails = {c.email.lower() for c in pend_changes if c.kind != "ADD"} | {c.new_value.lower() for c in pend_changes if c.kind == "FIX_EMAIL"}
    pend_add = {c.email.lower() for c in pend_changes if c.kind == "ADD"}

    seats = [p for p in Participant.objects.filter(shortlisted=True, license_state="NONE").select_related("region").order_by("region__sort_order", "name")
             if (SEAT_CODE, p.key) not in dismissed and not (p.cnic and p.cnic in pend_cnic) and (p.email or "").lower() not in pend_seat_emails]
    lics = [l for l in License.objects.filter(participant__shortlisted=False).select_related("participant").order_by("program_region", "name")
            if (LIC_CODE, f"{l.account}|{l.email}") not in dismissed and (l.account, l.email.lower()) not in pend_pairs and l.email.lower() not in pend_add]

    seat_rows = []
    for s in seats:
        opts = sorted((_pairing(s, l) for l in lics), key=lambda x: (-x["score"], x["lic"].name))
        seat_rows.append({"seat": s, "suggest": opts[0] if opts and opts[0]["score"] >= 0.8 else None, "options": opts[:40]})
    lic_rows = []
    for l in lics:
        opts = sorted((_pairing(s, l) for s in seats), key=lambda x: (-x["score"], x["seat"].name))
        lic_rows.append({"lic": l, "suggest": opts[0] if opts and opts[0]["score"] >= 0.8 else None, "options": opts[:40], "known_cnic": _lic_cnic(l)})

    other = []
    for i in DataIssue.objects.filter(data_import=imp).exclude(code__in=OWN_SECTIONS).order_by("severity", "code", "id")[:600]:
        if (i.code, i.ref) in dismissed:
            continue
        row = {"issue": i, "label": CODE_LABELS.get(i.code, i.code), "seats": [], "fix_seat": None}
        if i.code in ("FINAL_MULTI_EMAIL", "FINAL_DUP_ROW") and i.ref:
            row["seats"] = list(Participant.objects.filter(shortlisted=True, cnic=i.ref).select_related("region"))
        if i.code == "FINAL_BAD_CNIC":
            row["fix_seat"] = Participant.objects.filter(shortlisted=True, cnic__isnull=True, name=i.ref).first()
        other.append(row)
    groups = {}
    for r in other:
        groups.setdefault(r["issue"].code, {"label": r["label"], "code": r["issue"].code, "rows": []})["rows"].append(r)

    history = []
    for o in over:
        history.append({"kind": "override", "pk": o.pk, "when": o.created_at, "waiting": o.created_at > since,
                        "text": f"Merged licence {o.email} with the seat that has CNIC {o.cnic}"})
    for c in changes:
        text = {"ADD": f"Added {c.name or c.email} ({c.email}) to the Final list under {c.region}",
                "FIX_EMAIL": f"Corrected seat email {c.email} to {c.new_value}", "FIX_CNIC": f"Set CNIC {c.new_value} on the seat {c.email}",
                "REMOVE": f"Removed the seat {c.email} from the Final list"}[c.kind]
        history.append({"kind": "seat", "pk": c.pk, "when": c.created_at, "waiting": c.created_at > since, "text": text})
    for d in dismissals:
        history.append({"kind": "dismiss", "pk": d.pk, "when": d.created_at, "waiting": False,
                        "text": f"Marked as reviewed: {CODE_LABELS.get(d.code, d.code)}" + (f" - {d.ref.split('|')[-1]}" if d.ref else "")})
    history.sort(key=lambda h: h["when"], reverse=True)
    return {"seat_rows": seat_rows, "lic_rows": lic_rows, "groups": list(groups.values()), "history": history,
            "waiting": sum(1 for h in history if h["waiting"]), "regions": C.REGION_ORDER,
            "open_total": len(seat_rows) + len(lic_rows) + len(other), "other_total": len(other), "imp": imp}


def _replace(model, defaults=None, **lookup):
    """Save a decision as a NEW row (so its timestamp shows it is newer than the last import)."""
    model.objects.filter(**lookup).delete()
    return model.objects.create(**lookup, **(defaults or {}))


def _get(model, pk, what):
    try:
        return model.objects.get(pk=int(pk))
    except (TypeError, ValueError, model.DoesNotExist):
        raise ValueError(f"That {what} no longer exists. Reload the page.")


def perform(action, p, user):
    """Save one decision. Returns a short confirmation. Raises ValueError with a readable message."""
    if action == "merge":
        seat, lic = _get(Participant, p.get("seat_id"), "seat"), _get(License, p.get("lic_id"), "licence")
        if not seat.cnic:
            raise ValueError(f"{seat.name} has no CNIC on the Final list. Use 'Edit CNIC' first, then merge.")
        _replace(IdentityOverride, account=lic.account, email=lic.email.lower(),
                                                  defaults={"cnic": seat.cnic, "note": f"Merged on the Resolve queries page by {user}"})
        return f"Merged {lic.email} with {seat.name}. Press 'Apply & recalculate' to update the numbers."
    if action == "fix_email":
        seat, new = _get(Participant, p.get("seat_id"), "seat"), N.norm_email(p.get("value"))
        try:
            validate_email(new or "")
        except ValidationError:
            raise ValueError("That is not a valid email address.")
        old = (seat.email or "").lower()
        chained = SeatChange.objects.filter(kind="FIX_EMAIL", new_value=old).first()
        if chained:                      # the seat email was already corrected once: keep the ORIGINAL Final-list email as the key
            chained.delete()
            _replace(SeatChange, kind="FIX_EMAIL", email=chained.email, defaults={"new_value": new, "created_by": user})
        else:
            _replace(SeatChange, kind="FIX_EMAIL", email=old, defaults={"new_value": new, "created_by": user})
        return f"Seat email for {seat.name} set to {new}. Press 'Apply & recalculate'."
    if action == "fix_cnic":
        seat, cnic = _get(Participant, p.get("seat_id"), "seat"), N.norm_cnic(p.get("value"))
        if not cnic:
            raise ValueError("A CNIC has 13 digits (dashes are fine).")
        if not seat.email:
            raise ValueError("This seat has no email to attach the correction to.")
        _replace(SeatChange, kind="FIX_CNIC", email=seat.email.lower(), defaults={"new_value": cnic, "created_by": user})
        return f"CNIC for {seat.name} set. Press 'Apply & recalculate'."
    if action == "remove_seat":
        seat = _get(Participant, p.get("seat_id"), "seat")
        if not seat.email:
            raise ValueError("This seat has no email to attach the removal to.")
        _replace(SeatChange, kind="REMOVE", email=seat.email.lower(), defaults={"created_by": user})
        return f"{seat.name} ({seat.email}) will be taken off the Final list. Press 'Apply & recalculate'."
    if action == "add_seat":
        lic, region = _get(License, p.get("lic_id"), "licence"), p.get("region")
        if region not in C.REGION_ORDER:
            raise ValueError("Choose a region for the new seat.")
        cnic = N.norm_cnic(p.get("value")) if (p.get("value") or "").strip() else None
        if (p.get("value") or "").strip() and not cnic:
            raise ValueError("A CNIC has 13 digits (dashes are fine), or leave it empty.")
        _replace(SeatChange, kind="ADD", email=lic.email.lower(),
                                            defaults={"name": lic.name, "region": region, "new_value": cnic or "", "created_by": user})
        return f"{lic.name} will be added to the Final list under {region}. Press 'Apply & recalculate'."
    if action == "dismiss":
        code, ref = (p.get("code") or "").strip(), (p.get("ref") or "").strip()
        if not code:
            raise ValueError("Nothing to mark as reviewed.")
        _replace(Dismissal, code=code, ref=ref, defaults={"created_by": user})
        return "Marked as reviewed."
    if action == "dismiss_licence":
        lic = _get(License, p.get("lic_id"), "licence")
        _replace(Dismissal, code=LIC_CODE, ref=f"{lic.account}|{lic.email}", defaults={"created_by": user})
        return f"{lic.email} marked as reviewed (still not counted in any region)."
    if action == "dismiss_seat":
        seat = _get(Participant, p.get("seat_id"), "seat")
        _replace(Dismissal, code=SEAT_CODE, ref=seat.key, defaults={"created_by": user})
        return f"{seat.name} marked as reviewed (still counted as a seat without a licence)."
    if action == "undo":
        model = {"override": IdentityOverride, "seat": SeatChange, "dismiss": Dismissal}.get(p.get("kind"))
        if model is None:
            raise ValueError("Unknown decision.")
        _get(model, p.get("pk"), "decision").delete()
        return "Decision undone. Press 'Apply & recalculate' to update the numbers."
    raise ValueError("Unknown action.")
