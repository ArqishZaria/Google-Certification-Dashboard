"""The 10 downloadable reports required by the BRD (s.11). Pure Python: (columns, rows) from plain dicts."""
from __future__ import annotations

from .logic import catalog as C
from .logic import metrics as M

KPI_COLS = ["Registrations", "Screened", "Licences issued", "Accepted", "Pending", "Started", "Not started", "Active",
            "Inactive", "In progress", "Completed (PC)", "Completion % (of accepted)", "Acceptance %", "Avg PC progress %",
            "AI courses completed", "Learning hours"]


def _kpi_row(k):
    return [k["registrations"], k["screened"], k["licensed"], k["accepted"], k["pending"], k["started"], k["not_started"],
            k["active"], k["inactive"], k["in_progress"], k["completed"], k["completion_rate"], k["acceptance_rate"],
            k["avg_progress"], k["ai_courses_completed"], k["learning_hours"]]


def _group_report(ctx, key, label, order=None):
    rows = M.group_table(ctx["parts"], ctx["regs"], key, order)
    M.rank_rows(rows)
    return [label, "Rank (by completion %)"] + KPI_COLS, [[r["name"], r["rank"] or ""] + _kpi_row(r) for r in rows]


def national(ctx):
    return _group_report(ctx, "region", "Region", C.REGION_ORDER)


def regional(ctx):
    return _group_report(ctx, "council_label", "Local council (region)")


def local(ctx):
    return _group_report(ctx, "area_label", "Area (Jamatkhana) - local council")


PARTICIPANT_COLS = ["Name", "CNIC", "Email", "Phone", "Gender", "Region", "Local council", "Area", "License", "Status",
                    "Professional Certificate", "PC progress %", "PC completed", "AI courses completed", "Courses completed",
                    "Learning hours", "Last activity", "Days inactive"]


def _prow(p):
    return [p["name"], p["cnic"] or "", p["email"], p["phone"], p["gender"], p["region"], p["local_council"], p["area"],
            C.LICENSE_LABELS.get(p["license_state"], p["license_state"]), C.STATUS_LABELS.get(p["status"], p["status"]),
            p["pc_family"] or "", p["pc_progress"] if p["pc_progress"] is not None else "", "Yes" if p["pc_completed"] else "No",
            p["ai_completed"], p["courses_completed"], p["learning_hours"], p["last_activity"], p["days_inactive"] if p["days_inactive"] is not None else ""]


def participant(ctx):
    return PARTICIPANT_COLS, [_prow(p) for p in sorted(ctx["parts"], key=lambda p: (p["region"] or "", p["name"] or ""))]


def area(ctx):
    """Roster per area: who is in each Jamatkhana and how they are doing."""
    cols = ["Area - local council"] + PARTICIPANT_COLS
    rows = sorted(ctx["parts"], key=lambda p: (p.get("area_label") or "zzz", p["name"] or ""))
    return cols, [[p.get("area_label") or "Unassigned"] + _prow(p) for p in rows]


def completion(ctx):
    rows = [p for p in ctx["parts"] if p["status"] == "COMPLETED"]
    rows.sort(key=lambda p: (p["pc_completed_at"] is None, p["pc_completed_at"]))
    cols = ["Name", "Email", "Region", "Local council", "Area", "Professional Certificate(s) completed", "Completed on", "AI courses completed", "Learning hours"]
    return cols, [[p["name"], p["email"], p["region"], p["local_council"], p["area"], ", ".join(p["pc_completed_families"]),
                   p["pc_completed_at"], p["ai_completed"], p["learning_hours"]] for p in rows]


def pending(ctx):
    rows = [p for p in ctx["parts"] if p["status"] == "PENDING"]
    rows.sort(key=lambda p: (p["region"] or "", p["invited_at"] is None, p["invited_at"]))
    cols = ["Name", "Email", "Phone", "Region", "Local council", "Area", "Invited on", "Days since invited"]
    as_of = ctx.get("as_of")
    return cols, [[p["name"], p["email"], p["phone"], p["region"], p["local_council"], p["area"], p["invited_at"],
                   (as_of - p["invited_at"].date()).days if (as_of and p["invited_at"]) else ""] for p in rows]


def inactive(ctx):
    rows = [p for p in ctx["parts"] if p["activity"] == "INACTIVE"]
    rows.sort(key=lambda p: -(p["days_inactive"] or 0))
    cols = ["Name", "Email", "Phone", "Region", "Local council", "Area", "Professional Certificate", "PC progress %", "Last activity", "Days inactive"]
    return cols, [[p["name"], p["email"], p["phone"], p["region"], p["local_council"], p["area"], p["pc_family"] or "",
                   p["pc_progress"] if p["pc_progress"] is not None else "", p["last_activity"], p["days_inactive"]] for p in rows]


def ai_courses(ctx):
    parts = {p["key"]: p for p in ctx["parts"]}
    rows = []
    for s in ctx["specs"]:
        if s["category"] != C.AI:
            continue
        p = parts.get(s["participant_key"])
        if p:
            rows.append([p["name"], p["email"], p["region"], p["local_council"], s["family"], round(s["progress_pct"], 1),
                         "Yes" if s["completed"] else "No", s["completed_at"]])
    rows.sort(key=lambda r: (r[4], r[6] != "Yes", r[0] or ""))
    return ["Name", "Email", "Region", "Local council", "AI / Essentials course", "Progress %", "Completed", "Completed on"], rows


def excel_compatible(ctx):
    """The 2025 Excel dashboard cards per group, with that workbook's definitions (see Methodology)."""
    rows = M.legacy_group_table(ctx["parts"], ctx["specs"], "region", C.REGION_ORDER)
    tot = M.legacy_kpis(ctx["parts"], ctx["specs"])
    tot["name"] = "TOTAL"
    cols = ["Region", "Candidates invited", "Started any one course", "Not started any course", "Courses registered", "Candidates completed 1 course",
            "Total courses completed", "Candidates >50%", "Courses >50%", "Candidates <50%", "Courses <50%", "Candidates 0%", "Courses 0%"]
    f = lambda r: [r["name"], r["invited"], r["started"], r["not_started"], r["courses_registered"], r["completed_candidates"], r["courses_completed"],
                   r["gt50_candidates"], r["gt50_courses"], r["lt50_candidates"], r["lt50_courses"], r["zero_candidates"], r["zero_courses"]]
    return cols, [f(r) for r in rows] + [f(tot)]


def professional_certificates(ctx):
    pop = [r for r in M.course_popularity(ctx["specs"]) if r["category"] == C.PC]
    cols = ["Professional Certificate", "Participants enrolled", "Completed", "Completion %", "Avg progress %"] + [f"Progress {b}" for b in C.FINE_BANDS]
    return cols, [[r["family"], r["enrolled"], r["completed"], r["completion_rate"], r["avg_progress"]] + [r["bands"][b] for b in C.FINE_BANDS] for r in pop]


REPORTS = {
    "national": ("National Report", "KPIs by region, ranked.", national),
    "regional": ("Regional Report", "KPIs by local council (region shown in brackets).", regional),
    "local": ("Local Report", "KPIs by area (Jamatkhana) within each local council.", local),
    "area": ("Area Report", "Participant roster grouped by area (Jamatkhana).", area),
    "participant": ("Participant Report", "One row per participant.", participant),
    "completion": ("Completion Report", "Participants who completed a Professional Certificate.", completion),
    "pending": ("Pending License Report", "Licences issued but not yet accepted.", pending),
    "inactive": ("Inactive Learner Report", "Started learners with no recent activity.", inactive),
    "excel-compatible": ("Excel-compatible Report", "Region table with the 2025 Excel dashboard definitions.", excel_compatible),
    "ai-courses": ("AI Course Report", "AI / Essentials course enrolments and completions.", ai_courses),
    "professional-certificates": ("Professional Certificate Report", "Enrolment, completion and progress per certificate.", professional_certificates),
}


def followup_report(ctx, key):
    label, _ = M.FOLLOWUP_LISTS[key]
    rows = M.followup_list(ctx["parts"], key)
    rows.sort(key=lambda p: (-(p["days_inactive"] or 0), p["name"] or ""))
    cols = ["Name", "Email", "Phone", "Region", "Local council", "Area", "Status", "Professional Certificate", "PC progress %", "Last activity", "Days inactive"]
    return label, cols, [[p["name"], p["email"], p["phone"], p["region"], p["local_council"], p["area"],
                          C.STATUS_LABELS.get(p["status"], p["status"]), p["pc_family"] or "",
                          p["pc_progress"] if p["pc_progress"] is not None else "", p["last_activity"],
                          p["days_inactive"] if p["days_inactive"] is not None else ""] for p in rows]
