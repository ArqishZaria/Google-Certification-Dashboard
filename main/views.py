import os
import tempfile
from collections import Counter
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import exports, reports, services
from .logic import catalog as C
from .logic import metrics as M
from .models import (Area, DataImport, DataIssue, LocalCouncil, Participant, Region, Registration, SourceFile, Specialization)

GROUP_TITLES = {"region": "Region", "council_label": "Local council", "area_label": "Area (Jamatkhana)"}


def home(request):
    return render(request, "main/home.html")


# ----------------------------------------------------------------------------- decorators
def scoped(view):
    """Login required + resolve the user's data scope. No scope => friendly 403 page."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        eff = services.effective(request.user, request.GET)
        if eff is None:
            return render(request, "main/no_access.html", status=403)
        return view(request, eff, *args, **kwargs)
    return wrapper


def is_staff_user(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def filter_context(request, eff):
    """Options for the Region / Local council / Area dropdowns (limited to what the user may see)."""
    reg_q = Registration.objects.filter(eff["q"])
    regions = Region.objects.filter(id__in=reg_q.values("region_id")).order_by("sort_order")
    councils = LocalCouncil.objects.filter(id__in=reg_q.values("local_council_id")).select_related("region")
    if request.GET.get("region"):
        councils = councils.filter(region__name=request.GET["region"])
    area_list = []
    if request.GET.get("council", "").isdigit():
        area_list = Area.objects.filter(local_council_id=int(request.GET["council"]))
    return {"filter_regions": regions, "filter_councils": councils.order_by("region__sort_order", "name"), "filter_areas": area_list,
            "sel_programme": request.GET.get("programme", ""), "sel_region": request.GET.get("region", ""),
            "sel_council": request.GET.get("council", ""), "sel_area": request.GET.get("area", "")}


def base_ctx(request, eff, **extra):
    imp = DataImport.objects.first()
    ctx = {"eff": eff, "scope_label": eff["label"], "imp": imp, "data_alerts": (imp.stats or {}).get("alerts", []) if imp else [],
           "as_of": imp.as_of if imp else None,
           "can_import": is_staff_user(request.user), "is_national": eff["level"] == "national",
           "query": request.GET.urlencode()}
    ctx.update(filter_context(request, eff))
    ctx.update(extra)
    return ctx


# ----------------------------------------------------------------------------- dashboard
def chart_data(pay):
    stage = lambda rows: {"labels": [r["label"] for r in rows], "started": [r["started"] for r in rows],
                          "not_started": [r["not_started"] for r in rows], "pending": [r["pending"] for r in rows]}
    g = pay["groups"]
    pop = pay["popularity"]
    return {
        "funnel": {"labels": [f[0] for f in pay["funnel"]], "values": [f[1] for f in pay["funnel"]]},
        "status": {"labels": [s["label"] for s in pay["status"] if s["count"]], "values": [s["count"] for s in pay["status"] if s["count"]]},
        "groups": {"labels": [r["name"] for r in g], "licensed": [r["licensed"] for r in g], "accepted": [r["accepted"] for r in g],
                   "started": [r["started"] for r in g], "not_started": [r["not_started"] for r in g], "pending": [r["pending"] for r in g],
                   "completed": [r["completed"] for r in g], "completion_rate": [r["completion_rate"] for r in g],
                   "avg_progress": [r["avg_progress"] for r in g]},
        "group_bands": pay["group_bands"],
        "progress_dist": {"labels": [b for b, _ in pay["progress_dist"]], "values": [v for _, v in pay["progress_dist"]]},
        "popularity": {"labels": [r["family"] for r in pop], "completed": [r["completed"] for r in pop],
                       "incomplete": [r["incomplete"] for r in pop], "category": [r["category"] for r in pop]},
        "course_bands": {"labels": [r["family"] for r in pop], "bands": C.FINE_BANDS,
                         "series": {b: [r["bands"][b] for r in pop] for b in C.FINE_BANDS}},
        "cpl": {"labels": [str(k) for k, _ in pay["courses_per_learner"]], "values": [v for _, v in pay["courses_per_learner"]]},
        "gender": stage(pay["gender"]), "prof": stage(pay["prof_status"]), "income": stage(pay["income"]), "age": stage(pay["age"]),
        "edu_by_prof": pay["edu_by_prof"],
        "weekly": pay["weekly"],
        "monthly": {"labels": [m["month"] for m in pay["monthly"]], "licensed": [m["licensed"] for m in pay["monthly"]],
                    "accepted": [m["accepted"] for m in pay["monthly"]], "started": [m["started"] for m in pay["monthly"]],
                    "completed": [m["completed"] for m in pay["monthly"]]},
    }


@scoped
def dashboard(request, eff):
    imp = DataImport.objects.first()
    if imp is None:
        return render(request, "main/empty.html", base_ctx(request, eff))
    parts, regs, specs, as_of = services.load_scope_data(eff)
    gk = eff["group_key"]
    order = C.REGION_ORDER if gk == "region" else None
    pay = M.dashboard_payload(parts, regs, specs, as_of, gk, order)
    heat = pay["heatmap"]
    heat_rows = list(zip(heat["groups"], heat["matrix"]))
    return render(request, "main/dashboard.html", base_ctx(
        request, eff, pay=pay, k=pay["kpis"], charts=chart_data(pay), heat=heat, heat_rows=heat_rows,
        group_title=GROUP_TITLES[gk], inactivity_days=imp.inactivity_days, nrows=len(parts)))


# ----------------------------------------------------------------------------- participants
def apply_participant_filters(qs, g):
    q = (g.get("q") or "").strip()
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        cond = Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(coursera_external_id__icontains=q)
        if len(digits) >= 4:
            cond |= Q(cnic__icontains=digits) | Q(phone__icontains=digits)
        if digits and digits == q and len(digits) < 8:
            cond |= Q(registration__form_id=int(digits))
        qs = qs.filter(cond)
    for param, field in (("gender", "gender"), ("status", "status"), ("license", "license_state"), ("band", "progress_band"), ("activity", "activity")):
        if g.get(param):
            qs = qs.filter(**{field: g[param]})
    if g.get("course"):
        qs = qs.filter(spec_enrollments__specialization__family=g["course"])
    if g.get("pc"):
        qs = qs.filter(spec_enrollments__specialization__family=g["pc"], spec_enrollments__specialization__category=C.PC)
    ai = g.get("ai")
    if ai == "enrolled":
        qs = qs.filter(ai_enrolled__gt=0)
    elif ai == "completed":
        qs = qs.filter(ai_completed__gt=0)
    elif ai == "none":
        qs = qs.filter(ai_enrolled=0)
    field = {"invited": "invited_at", "accepted": "joined_at", "started": "first_enrolled_at", "completed": "pc_completed_at", "activity": "last_activity"}.get(g.get("date_type"))
    if field:
        for param, op in (("date_from", "gte"), ("date_to", "lte")):
            try:
                d = date.fromisoformat(g.get(param) or "")
            except ValueError:
                continue          # ignore malformed dates instead of erroring
            qs = qs.filter(**{f"{field}__date__{op}": d})
    return qs.distinct()


@scoped
def participant_list(request, eff):
    qs = apply_participant_filters(Participant.objects.filter(eff["q"]).select_related("region", "local_council", "area"), request.GET)
    page = Paginator(qs.order_by("name", "id"), 50).get_page(request.GET.get("page"))
    keep = request.GET.copy()
    keep.pop("page", None)
    families = Specialization.objects.order_by("category", "family")
    return render(request, "main/participants.html", base_ctx(
        request, eff, page=page, total=page.paginator.count, qs_keep=keep.urlencode(), g=request.GET,
        all_families=sorted({s.family for s in families}), pc_families=sorted({s.family for s in families if s.category == C.PC}),
        status_choices=[(k, C.STATUS_LABELS[k]) for k in C.STATUS_ORDER], band_choices=C.BRD_BANDS + [C.NO_PC],
        date_types=[("invited", "Licence issued"), ("accepted", "Licence accepted"), ("started", "First enrolment"), ("completed", "PC completed"), ("activity", "Last activity")]))


@scoped
def participant_detail(request, eff, public_id):
    p = get_object_or_404(Participant.objects.filter(eff["q"]).select_related("region", "local_council", "area", "registration"), public_id=public_id)
    return render(request, "main/participant_detail.html", base_ctx(
        request, eff, p=p, reg=p.registration, licenses=p.licenses.all(),
        specs=p.spec_enrollments.select_related("specialization").order_by("specialization__category", "-progress_pct"),
        courses=p.course_enrollments.order_by("-completed", "-progress"), issues=p.issues.all()))


# ----------------------------------------------------------------------------- follow-up lists
@scoped
def followup(request, eff):
    parts, regs, specs, as_of = services.load_scope_data(eff)
    key = request.GET.get("list", "pending")
    if key not in M.FOLLOWUP_LISTS:
        key = "pending"
    counts = [(k, v[0], len(M.followup_list(parts, k))) for k, v in M.FOLLOWUP_LISTS.items()]
    rows = sorted(M.followup_list(parts, key), key=lambda p: (-(p["days_inactive"] or 0), p["name"] or ""))
    return render(request, "main/followup.html", base_ctx(request, eff, key=key, counts=counts, title=M.FOLLOWUP_LISTS[key][0], rows=rows[:500], total=len(rows)))


# ----------------------------------------------------------------------------- reports
@scoped
def report_index(request, eff):
    return render(request, "main/reports.html", base_ctx(
        request, eff, report_list=[(slug, t, d) for slug, (t, d, _) in reports.REPORTS.items()],
        followups=[(k, v[0]) for k, v in M.FOLLOWUP_LISTS.items()]))


def _report_data(eff, slug):
    parts, regs, specs, as_of = services.load_scope_data(eff)
    ctx = {"parts": parts, "regs": regs, "specs": specs, "as_of": as_of}
    if slug.startswith("followup-"):
        key = slug[len("followup-"):]
        if key not in M.FOLLOWUP_LISTS:
            raise Http404("Unknown list")
        title, cols, rows = reports.followup_report(ctx, key)
        return "Follow-up: " + title, "", cols, rows, as_of
    if slug not in reports.REPORTS:
        raise Http404("Unknown report")
    title, desc, fn = reports.REPORTS[slug]
    cols, rows = fn(ctx)
    return title, desc, cols, rows, as_of


@scoped
def report_view(request, eff, slug):
    title, desc, cols, rows, as_of = _report_data(eff, slug)
    return render(request, "main/report_preview.html", base_ctx(request, eff, slug=slug, title=title, desc=desc, cols=cols, rows=rows[:100], total=len(rows)))


@scoped
def report_download(request, eff, slug, fmt):
    if fmt not in ("csv", "xlsx", "pdf"):
        raise Http404("Unknown format")
    title, desc, cols, rows, as_of = _report_data(eff, slug)
    subtitle = f"Scope: {eff['label']}  |  Data as of {as_of:%d %b %Y}  |  {len(rows)} rows" if as_of else eff["label"]
    return exports.respond(fmt, title, subtitle, cols, rows)


# ----------------------------------------------------------------------------- data quality / import / methodology
@scoped
def data_quality(request, eff):
    if eff["level"] != "national":
        return render(request, "main/no_access.html", status=403)
    imp = DataImport.objects.first()
    sev = request.GET.get("severity", "")
    issues = DataIssue.objects.select_related("participant").filter(data_import=imp) if imp else DataIssue.objects.none()
    summary = Counter(i.severity for i in issues)
    codes = Counter(i.code for i in issues)
    if sev:
        issues = issues.filter(severity=sev)
    issues = issues.order_by(Case(When(severity="ERROR", then=Value(0)), When(severity="WARNING", then=Value(1)), default=Value(2), output_field=IntegerField()), "code", "id")
    unreviewed = Specialization.objects.filter(reviewed=False)
    recon_bad = [c for c in (imp.recon if imp else []) if c.get("ok") is False]
    return render(request, "main/data_quality.html", base_ctx(
        request, eff, issues=issues[:500], issue_total=issues.count(), summary=dict(summary), codes=sorted(codes.items()),
        sev=sev, unreviewed=unreviewed, recon_bad=recon_bad, catalogue=Specialization.objects.all()))


@login_required
def import_data(request):
    if not is_staff_user(request.user):
        return render(request, "main/no_access.html", status=403)
    eff = services.effective(request.user, {}) or {"label": "National", "level": "national", "q": Q(), "group_key": "region"}
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "Please choose the files to import.")
        else:
            with tempfile.TemporaryDirectory() as tmp:
                pairs = []
                for i, f in enumerate(files):
                    safe = os.path.basename(f.name)
                    os.makedirs(os.path.join(tmp, str(i)), exist_ok=True)      # same file name twice must not overwrite
                    path = os.path.join(tmp, str(i), safe)
                    with open(path, "wb") as out:
                        for chunk in f.chunks():
                            out.write(chunk)
                    pairs.append((path, safe))
                try:
                    imp, stored, skipped = services.add_source_files(pairs, request.user, allow_fewer_accounts=bool(request.POST.get("allow_fewer")))
                except ValueError as e:
                    messages.error(request, str(e))
                except Exception as e:  # nothing is changed: add_source_files is atomic
                    messages.error(request, f"Import failed and NOTHING was changed: {e}")
                else:
                    for name, why in skipped:
                        messages.warning(request, f"{name}: skipped - {why}.")
                    if imp:
                        messages.success(request, f"Added {len(stored)} file(s). Data as of {imp.as_of:%d %b %Y}: {imp.stats.get('participants_licensed', 0)} licensed participants.")
                        return redirect("data_quality")
                    if stored:
                        messages.info(request, f"Added {len(stored)} file(s). The dashboard will update once all required files are loaded (see the checklist below).")
                    return redirect("import_data")
    return render(request, "main/import.html", base_ctx(request, eff, **import_context()))


def import_context():
    loaded = list(SourceFile.objects.defer("content"))
    kinds = {f.kind for f in loaded}
    checklist = [("Registration master workbook", "master" in kinds),
                 ("Final list workbook", "final" in kinds),
                 ("Coursera Program Activity CSV (at least one account)", "program" in kinds)]
    return {"source_files": loaded, "checklist": checklist,
            "kind_labels": {"master": "Registration master", "final": "Final list", "program": "Coursera Program", "spec": "Coursera Specialisation",
                            "course": "Coursera Course", "summary": "Coursera totals"}}


@require_POST
@login_required
def remove_source(request, pk):
    if not is_staff_user(request.user):
        return render(request, "main/no_access.html", status=403)
    try:
        f, imp = services.remove_source_file(pk, request.user)
    except SourceFile.DoesNotExist:
        messages.error(request, "That file was already removed.")
    except Exception as e:
        messages.error(request, f"Could not remove the file; NOTHING was changed: {e}")
    else:
        if imp:
            messages.success(request, f"Removed {f.name}. Dashboard recalculated from the remaining files.")
        else:
            messages.warning(request, f"Removed {f.name}. A required file is now missing, so the dashboard is empty until it is uploaded again.")
    return redirect("import_data")


@require_POST
@login_required
def recalculate(request):
    if not is_staff_user(request.user):
        return render(request, "main/no_access.html", status=403)
    n = services.recompute_all()
    messages.success(request, f"Recalculated {n} participants with the current specialization categories.")
    return redirect("data_quality")


@scoped
def methodology(request, eff):
    return render(request, "main/methodology.html", base_ctx(
        request, eff, catalogue=Specialization.objects.order_by("category", "family"), bands=C.BRD_BANDS, fine=C.FINE_BANDS,
        inactivity=(DataImport.objects.first().inactivity_days if DataImport.objects.exists() else 14)))
