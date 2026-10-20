"""All dashboard business rules in ONE place (pure Python, no Django).

Definitions (also rendered on the in-app "Methodology" page):

  Participant            one real person (identified by CNIC), 1..n Coursera licences (normally 1)
  License state          ACCEPTED (Coursera MEMBER) | PENDING (INVITED) | NONE | REMOVED | OTHER
  Started                enrolled in >=1 specialization OR course
  Professional Cert (PC) a specialization whose category is PC (see catalog.SPEC_CATALOG / admin)
  Completed              Coursera marks >=1 PC as Completed.  AI / short courses NEVER count (Rule 3)
  PC progress %          100 if completed, else best PC's completed_courses / total_courses (Rule 4)
  Active learner         accepted, not completed, started, last activity <= N days before data snapshot
  Inactive learner       accepted, not completed, started, last activity  > N days before data snapshot
  Not started            accepted, never enrolled
  => accepted = completed + active + inactive + not_started   (checked in tests)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from . import catalog as C

DEFAULT_INACTIVITY_DAYS = 14


def _ts(dt):
    return dt.timestamp() if dt else float("-inf")


def _max(values):
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def _min(values):
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


# =========================================================================== per participant
def derive_participant(licenses, specs, courses, as_of: date, inactivity_days=DEFAULT_INACTIVITY_DAYS):
    """licenses: [{state, removed, invited_at, joined_at, last_activity_at, learning_hours}]
       specs:    [{family, category, progress_pct, completed, completed_at, enrolled_at, last_activity_at}]
       courses:  [{completed, completed_at, enrolled_at, last_activity_at}]
    Returns a dict of derived fields (all JSON/DB friendly)."""
    live = [l for l in licenses if not l.get("removed")]
    if not licenses:
        license_state = "NONE"
    elif any(l["state"] == "ACCEPTED" for l in live):
        license_state = "ACCEPTED"
    elif any(l["state"] == "PENDING" for l in live):
        license_state = "PENDING"
    elif not live:
        license_state = "REMOVED"
    else:
        license_state = "OTHER"

    pcs = [s for s in specs if s["category"] == C.PC]
    ais = [s for s in specs if s["category"] == C.AI]
    shorts = [s for s in specs if s["category"] == C.SHORT]

    started = bool(specs or courses)
    spec_rows = len(specs)
    spec_rows_done = sum(1 for s in specs if s["completed"])
    best_spec_pct = None
    if specs:
        best_spec_pct = 100.0 if spec_rows_done else round(max((s["progress_pct"] or 0.0) for s in specs), 2)

    primary = None
    if pcs:
        primary = max(pcs, key=lambda s: (bool(s["completed"]), s["progress_pct"] or 0.0, _ts(s.get("last_activity_at"))))
    pc_completed = any(s["completed"] for s in pcs)
    if pc_completed:
        pc_progress = 100.0
    elif primary is not None:
        pc_progress = round(min(100.0, max(0.0, primary["progress_pct"] or 0.0)), 2)
    else:
        pc_progress = None
    pc_family = primary["family"] if primary else None
    pc_completed_families = sorted({s["family"] for s in pcs if s["completed"]})
    pc_completed_at = _min([s.get("completed_at") for s in pcs if s["completed"]])

    activity_ts = []
    for s in specs:
        activity_ts += [s.get("last_activity_at"), s.get("enrolled_at")]
    for c in courses:
        activity_ts += [c.get("last_activity_at"), c.get("enrolled_at")]
    for l in licenses:
        activity_ts.append(l.get("last_activity_at"))
    last_activity = _max(activity_ts) if started else _max([l.get("last_activity_at") for l in licenses])
    first_enrolled_at = _min([s.get("enrolled_at") for s in specs] + [c.get("enrolled_at") for c in courses])

    days_inactive = None
    if last_activity is not None:
        days_inactive = max(0, (as_of - last_activity.date()).days)

    # ---- status (mutually exclusive) and activity bucket
    if pc_completed:
        status = "COMPLETED"
    elif license_state == "NONE":
        status = "NO_LICENSE"
    elif license_state == "PENDING":
        status = "PENDING"
    elif license_state != "ACCEPTED":
        status = "OTHER"
    elif not started:
        status = "NOT_STARTED"
    elif pcs:
        status = "IN_PROGRESS"
    else:
        status = "NO_PC"

    if status == "NOT_STARTED":
        activity = "NOT_STARTED"
    elif status in ("IN_PROGRESS", "NO_PC"):
        activity = "ACTIVE" if (days_inactive is not None and days_inactive <= inactivity_days) else "INACTIVE"
    else:
        activity = None

    return {
        "license_state": license_state,
        "license_count": len(licenses),
        "started": started,
        "status": status,
        "activity": activity,
        "pc_progress": pc_progress,
        "pc_family": pc_family,
        "pc_completed": pc_completed,
        "pc_completed_at": pc_completed_at,
        "pc_completed_families": pc_completed_families,
        "pc_enrolled": len({s["family"] for s in pcs}),
        "ai_enrolled": len({s["family"] for s in ais}),
        "ai_completed": len({s["family"] for s in ais if s["completed"]}),
        "short_enrolled": len({s["family"] for s in shorts}),
        "short_completed": len({s["family"] for s in shorts if s["completed"]}),
        "specs_enrolled": len({s["family"] for s in specs}),
        "courses_enrolled": len(courses),
        "courses_completed": sum(1 for c in courses if c["completed"]),
        "learning_hours": round(sum((l.get("learning_hours") or 0.0) for l in licenses), 2),
        "last_activity": last_activity,
        "first_enrolled_at": first_enrolled_at,
        "invited_at": _min([l.get("invited_at") for l in licenses]),
        "joined_at": _min([l.get("joined_at") for l in live]),
        "days_inactive": days_inactive,
        "progress_band": C.brd_band(pc_progress),
        "fine_band": C.fine_band(pc_progress),
        "spec_rows": spec_rows,
        "spec_rows_done": spec_rows_done,
        "best_spec_pct": best_spec_pct,
        "legacy_band": C.legacy_band(best_spec_pct, spec_rows_done > 0),
    }


# =========================================================================== follow-up lists (BRD s.12)
def _accepted_open(p):
    return p["license_state"] == "ACCEPTED" and p["status"] != "COMPLETED"


FOLLOWUP_LISTS = {
    "pending": ("Licence not accepted", lambda p: p["status"] == "PENDING"),
    "not_started": ("Accepted but not started learning", lambda p: p["status"] == "NOT_STARTED"),
    "inactive": ("Inactive learners", lambda p: p["activity"] == "INACTIVE"),
    "below_25": ("Below 25% (Professional Certificate)",
                 lambda p: _accepted_open(p) and p["started"] and p["pc_progress"] is not None and p["pc_progress"] < 25),
    "stuck_50": ("Stuck around 50% (40-59%, inactive)",
                 lambda p: _accepted_open(p) and p["pc_progress"] is not None and 40 <= p["pc_progress"] < 60 and p["activity"] == "INACTIVE"),
    "above_75": ("Above 75% - close to completion",
                 lambda p: _accepted_open(p) and p["pc_progress"] is not None and 75 <= p["pc_progress"] < 100),
    "completed": ("Completed a Professional Certificate", lambda p: p["status"] == "COMPLETED"),
}


def followup_list(parts, key):
    label, pred = FOLLOWUP_LISTS[key]
    return [p for p in parts if pred(p)]


# =========================================================================== aggregations
def _pct(a, b):
    return round(a * 100.0 / b, 1) if b else 0.0


def kpis(parts, registrations=0):
    """Headline numbers for any set of participants (national / region / council / area)."""
    c = Counter(p["status"] for p in parts)
    act = Counter(p["activity"] for p in parts)
    accepted = sum(1 for p in parts if p["license_state"] == "ACCEPTED")
    licensed = sum(1 for p in parts if p["license_state"] != "NONE")
    pending = c["PENDING"]
    completed = c["COMPLETED"]
    started = sum(1 for p in parts if p["started"])
    pc_prog = [p["pc_progress"] for p in parts if p["pc_progress"] is not None]
    return {
        "registrations": registrations,
        "screened": sum(1 for p in parts if p["shortlisted"]),
        "licensed": licensed,
        "accepted": accepted,
        "pending": pending,
        "started": started,
        "not_started": act["NOT_STARTED"],
        "active": act["ACTIVE"],
        "inactive": act["INACTIVE"],
        "in_progress": c["IN_PROGRESS"] + c["NO_PC"],      # started, not yet completed (any learning)
        "pc_in_progress": c["IN_PROGRESS"],                 # ... of which enrolled in a Professional Certificate
        "no_pc": c["NO_PC"],
        "completed": completed,
        "no_license": c["NO_LICENSE"],
        "pc_enrolled": len(pc_prog),
        "completion_rate": _pct(completed, accepted),
        "acceptance_rate": _pct(accepted, licensed),
        "activation_rate": _pct(started, licensed),
        "avg_progress": round(sum(pc_prog) / len(pc_prog), 1) if pc_prog else 0.0,
        "ai_learners_completed": sum(1 for p in parts if p["ai_completed"] > 0),
        "ai_courses_completed": sum(p["ai_completed"] for p in parts),
        "short_courses_completed": sum(p["short_completed"] for p in parts),
        "courses_completed": sum(p["courses_completed"] for p in parts),
        "learning_hours": round(sum(p["learning_hours"] for p in parts), 1),
        "national_staff": sum(1 for p in parts if p.get("programme_region") == "National" and p["license_state"] != "NONE"),
        "licensed_not_shortlisted": sum(1 for p in parts if p["license_state"] != "NONE" and not p["shortlisted"]),
    }


def group_table(parts, regs, key, order=None):
    """KPI rows grouped by `key` (e.g. 'region', 'local_council', 'area').
    `regs` is the list of registration dicts (they carry the same grouping keys)."""
    gp, gr = defaultdict(list), Counter()
    for p in parts:
        gp[p.get(key) or "Unassigned"].append(p)
    for r in regs:
        gr[r.get(key) or "Unassigned"] += 1
    names = set(gp) | set(gr)
    rows = []
    for name in names:
        k = kpis(gp.get(name, []), gr.get(name, 0))
        k["name"] = name
        rows.append(k)
    if order:
        pos = {n: i for i, n in enumerate(order)}
        rows.sort(key=lambda r: (pos.get(r["name"], 999), r["name"]))
    else:
        rows.sort(key=lambda r: (-r["accepted"], r["name"]))
    return rows


def rank_rows(rows, min_accepted=1):
    """Dense rank by completion rate, then avg progress, then accepted. Adds 'rank' (None if no accepted)."""
    ranked = sorted((r for r in rows if r["accepted"] >= min_accepted),
                    key=lambda r: (-r["completion_rate"], -r["avg_progress"], -r["accepted"], r["name"]))
    last, rank = None, 0
    for i, r in enumerate(ranked, 1):
        sig = (r["completion_rate"], r["avg_progress"])
        if sig != last:
            rank, last = i, sig
        r["rank"] = rank
    for r in rows:
        r.setdefault("rank", None)
    return ranked


def _count_by(parts, key_fn, order=None):
    c = Counter(key_fn(p) for p in parts)
    if order:
        out = [(k, c.get(k, 0)) for k in order if k in c or True]
        extra = [(k, v) for k, v in c.items() if k not in order]
        return out + sorted(extra)
    return sorted(c.items(), key=lambda kv: -kv[1])


def stage_split(parts, key_fn, order=None):
    """For stacked 'started / accepted-not-started / pending' bars (licensed participants only)."""
    labels = {}
    for p in parts:
        if p["license_state"] == "NONE":
            continue
        k = key_fn(p) or "Data not available"
        d = labels.setdefault(k, {"started": 0, "not_started": 0, "pending": 0, "total": 0})
        if p["started"]:
            d["started"] += 1
        elif p["license_state"] == "PENDING":
            d["pending"] += 1
        else:
            d["not_started"] += 1
        d["total"] += 1
    keys = list(order) if order else sorted(labels, key=lambda k: -labels[k]["total"])
    keys = [k for k in keys if k in labels] + [k for k in labels if k not in keys]
    return [{"label": k, **labels[k]} for k in keys]


def funnel(parts, regs_total):
    k = kpis(parts, regs_total)
    return [
        ("Registered", regs_total),
        ("Screened (shortlisted)", k["screened"]),
        ("Licence issued", k["licensed"]),
        ("Licence accepted", k["accepted"]),
        ("Started learning", k["started"]),
        ("Enrolled in a Professional Certificate", k["pc_enrolled"]),
        ("Completed Professional Certificate", k["completed"]),
    ]


def progress_distribution(parts):
    """BRD bands over participants enrolled in a PC (+ the 'no PC' remainder among started)."""
    c = Counter(p["progress_band"] for p in parts if p["license_state"] == "ACCEPTED" and p["pc_progress"] is not None)
    return [(b, c.get(b, 0)) for b in C.BRD_BANDS]


def zero_progress_count(parts):
    return sum(1 for p in parts if p["license_state"] == "ACCEPTED" and p["pc_progress"] is not None and p["pc_progress"] == 0)


LOCAL_UTC_OFFSET_HOURS = 5   # Pakistan Standard Time (no DST): months/weeks are bucketed in local time, not UTC


def _local(dt):
    return dt + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)


def _month(dt):
    return _local(dt).strftime("%Y-%m") if dt else None


def _week(dt):
    if not dt:
        return None
    ld = _local(dt)
    return (ld.date() - timedelta(days=ld.weekday())).isoformat()


def monthly_growth(parts, regs):
    series = {
        "registrations": Counter(_month(r.get("created_at")) for r in regs),
        "licensed": Counter(_month(p.get("invited_at")) for p in parts),
        "accepted": Counter(_month(p.get("joined_at")) for p in parts),
        "started": Counter(_month(p.get("first_enrolled_at")) for p in parts),
        "completed": Counter(_month(p.get("pc_completed_at")) for p in parts if p["pc_completed"]),
    }
    months = sorted({m for c in series.values() for m in c if m})
    rows = []
    prev = {}
    for m in months:
        row = {"month": m}
        for k, c in series.items():
            v = c.get(m, 0)
            row[k] = v
            pv = prev.get(k)
            row[k + "_growth"] = round((v - pv) * 100.0 / pv, 1) if pv else None
            prev[k] = v
        rows.append(row)
    return rows


def weekly_trend(parts):
    series = {
        "accepted": Counter(_week(p.get("joined_at")) for p in parts),
        "started": Counter(_week(p.get("first_enrolled_at")) for p in parts),
        "completed": Counter(_week(p.get("pc_completed_at")) for p in parts if p["pc_completed"]),
    }
    weeks = sorted({w for c in series.values() for w in c if w})
    return {"weeks": weeks, **{k: [series[k].get(w, 0) for w in weeks] for k in series}}


def leaderboard(parts, n=15):
    elig = [p for p in parts if p["license_state"] == "ACCEPTED" and p["started"]]
    elig.sort(key=lambda p: (-(p["pc_completed"]), -(p["pc_progress"] if p["pc_progress"] is not None else -1),
                             -p["courses_completed"], -p["learning_hours"], p["name"] or ""))
    return elig[:n]


# ---------------------------------------------------------------- enrolment-level aggregations
def course_popularity(specs):
    """specs: rows with participant_key, family, category, completed, progress_pct. One row per (participant, spec)."""
    fam = {}
    for s in specs:
        d = fam.setdefault(s["family"], {"family": s["family"], "category": s["category"], "people": set(), "done": set(), "bands": Counter(), "prog": []})
        d["people"].add(s["participant_key"])
        d["prog"].append(s["progress_pct"] if not s["completed"] else 100.0)
        if s["completed"]:
            d["done"].add(s["participant_key"])
        d["bands"][C.fine_band(100.0 if s["completed"] else s["progress_pct"])] += 1
    out = []
    for d in fam.values():
        n = len(d["people"])
        out.append({
            "family": d["family"], "category": d["category"], "enrolled": n, "completed": len(d["done"]),
            "incomplete": n - len(d["done"]), "completion_rate": _pct(len(d["done"]), n),
            "avg_progress": round(sum(d["prog"]) / len(d["prog"]), 1) if d["prog"] else 0.0,
            "bands": {b: d["bands"].get(b, 0) for b in C.FINE_BANDS},
        })
    out.sort(key=lambda r: (-r["enrolled"], r["family"]))
    return out


def heatmap(specs, group_key, groups=None):
    """Group x family matrix of distinct enrolled participants (+completions)."""
    cells = defaultdict(lambda: {"enrolled": set(), "completed": set()})
    fams = Counter()
    for s in specs:
        g = s.get(group_key) or "Unassigned"
        cells[(g, s["family"])]["enrolled"].add(s["participant_key"])
        if s["completed"]:
            cells[(g, s["family"])]["completed"].add(s["participant_key"])
    for (g, f), v in cells.items():
        fams[f] += len(v["enrolled"])
    families = [f for f, _ in sorted(fams.items(), key=lambda kv: (-kv[1], kv[0]))]
    gl = list(groups) if groups else sorted({g for g, _ in cells})
    gl = [g for g in gl if any((g, f) in cells for f in families)]
    matrix = [[{"enrolled": len(cells[(g, f)]["enrolled"]), "completed": len(cells[(g, f)]["completed"])} if (g, f) in cells else {"enrolled": 0, "completed": 0}
               for f in families] for g in gl]
    mx = max((c["enrolled"] for row in matrix for c in row), default=0)
    return {"groups": gl, "families": families, "matrix": matrix, "max": mx}


def courses_per_learner(parts):
    """Distribution: how many specializations each started participant enrolled in (old chart 7)."""
    c = Counter(p["specs_enrolled"] for p in parts if p["started"])
    return sorted(c.items())


def category_summary(specs):
    out = {}
    for cat in (C.PC, C.AI, C.SHORT):
        rows = [s for s in specs if s["category"] == cat]
        people = {s["participant_key"] for s in rows}
        done = {(s["participant_key"], s["family"]) for s in rows if s["completed"]}
        out[cat] = {"label": C.CATEGORY_LABELS[cat], "enrolled_people": len(people), "enrolments": len({(s["participant_key"], s["family"]) for s in rows}), "completions": len(done)}
    return out


# =========================================================================== full dashboard payload
def cross_tab(parts, row_fn, col_fn, row_order=None, col_order=None, only=lambda p: p["license_state"] != "NONE"):
    cnt = Counter()
    rows_seen, cols_seen = set(), set()
    for p in parts:
        if not only(p):
            continue
        r, c = row_fn(p) or "Data not available", col_fn(p) or "Data not available"
        cnt[(r, c)] += 1
        rows_seen.add(r)
        cols_seen.add(c)
    rows = [r for r in (row_order or []) if r in rows_seen] + sorted(rows_seen - set(row_order or []))
    cols = [c for c in (col_order or []) if c in cols_seen] + sorted(cols_seen - set(col_order or []))
    return {"rows": rows, "cols": cols, "cells": [[cnt.get((r, c), 0) for c in cols] for r in rows]}


def group_band_matrix(parts, key, order=None):
    """Per group: accepted participants split by progress band (replaces the 100% / >50% / <50% / 0% regional charts)."""
    bands = C.BRD_BANDS + [C.NO_PC, "Not started"]
    cnt = defaultdict(Counter)
    for p in parts:
        if p["license_state"] != "ACCEPTED":
            continue
        g = p.get(key) or "Unassigned"
        b = "Not started" if not p["started"] else p["progress_band"]
        cnt[g][b] += 1
    groups = [g for g in (order or []) if g in cnt] + sorted(set(cnt) - set(order or []))
    return {"groups": groups, "bands": bands, "series": {b: [cnt[g].get(b, 0) for g in groups] for b in bands}}


def dashboard_payload(parts, regs, specs, as_of, group_key="region", group_order=None, top_n=10):
    """Everything the dashboard page needs, from plain dict inputs."""
    k = kpis(parts, len(regs))
    rows = group_table(parts, regs, group_key, group_order)
    ranked = rank_rows(rows)
    pop = course_popularity(specs)
    status_counts = Counter(p["status"] for p in parts)
    gender_split = stage_split(parts, lambda p: p["gender"], ["Male", "Female"])
    return {
        "as_of": as_of,
        "kpis": k,
        "funnel": funnel(parts, len(regs)),
        "status": [{"key": s, "label": C.STATUS_LABELS[s], "count": status_counts.get(s, 0)} for s in C.STATUS_ORDER],
        "progress_dist": progress_distribution(parts),
        "zero_progress": zero_progress_count(parts),
        "groups": rows,
        "group_key": group_key,
        "top": ranked[:3],
        "bottom": [r for r in ranked[::-1] if r not in ranked[:3]][:3] if len(ranked) > 3 else [],
        "group_bands": group_band_matrix(parts, group_key, group_order),
        "popularity": pop,
        "category_summary": category_summary(specs),
        "heatmap": heatmap(specs, group_key, [r["name"] for r in rows]),
        "courses_per_learner": courses_per_learner(parts),
        "gender": gender_split,
        "prof_status": stage_split(parts, lambda p: p["prof_status"], C.PROF_STATUSES),
        "income": stage_split(parts, lambda p: p["income_band"], C.INCOME_BANDS + [C.INCOME_UNKNOWN]),
        "age": stage_split(parts, lambda p: p["age_bracket"], C.AGE_BRACKETS),
        "edu_by_prof": cross_tab(parts, lambda p: p["education_group"], lambda p: p["prof_status"], C.EDUCATION_GROUPS, C.PROF_STATUSES),
        "age_by_prof": cross_tab(parts, lambda p: p["age_bracket"], lambda p: p["prof_status"], C.AGE_BRACKETS, C.PROF_STATUSES),
        "monthly": monthly_growth(parts, regs),
        "weekly": weekly_trend(parts),
        "leaderboard": leaderboard(parts, top_n),
        "most_inactive": sorted([p for p in parts if p["activity"] == "INACTIVE"], key=lambda p: -(p["days_inactive"] or 0))[:top_n],
        "legacy": legacy_kpis(parts, specs),
        "legacy_groups": legacy_group_table(parts, specs, group_key, group_order),
        "below_20": sum(1 for p in parts if p["license_state"] == "ACCEPTED" and p["pc_progress"] is not None and p["pc_progress"] < 20),
    }


def enrich_specs(spec_rows, parts):
    """Attach the participant's geography to each enrolment row (so heat-maps can group by region/council/area)."""
    by_key = {p["key"]: p for p in parts}
    out = []
    for s in spec_rows:
        p = by_key.get(s["participant_key"])
        if p is None:
            continue  # enrolment belongs to someone outside the current scope
        out.append({**s, "region": p.get("region"), "local_council": p.get("local_council"), "area": p.get("area"),
                    "council_label": p.get("council_label"), "area_label": p.get("area_label")})
    return out


def add_geo_labels(rows):
    """Local-council and Jamatkhana names are NOT unique (e.g. 'Karimabad' is a council in two regions, 'Alyabad'
    a Jamatkhana in four councils). Group on these disambiguated labels, never on the bare names."""
    for r in rows:
        lc, a = r.get("local_council"), r.get("area")
        reg = r.get("council_region") or r.get("residing_region")
        r["council_label"] = (f"{lc} ({reg})" if reg else lc) if lc else None
        r["area_label"] = (f"{a} - {lc}" if lc else a) if a else None
    return rows


# =========================================================================== Excel-compatible ("2025 workbook") metrics
def legacy_kpis(parts, specs):
    """The numbers on the 2025 Excel dashboard, computed with ITS definitions:
       invited  = everyone holding a licence;  started = enrolled in >=1 SPECIALIZATION;  not started = invited - started
       courses registered = specialization enrolments;  completed = ANY specialization (AI Essentials included)
       candidate bands = each started candidate counted ONCE by their most advanced specialization;
       course bands    = every specialization enrolment in that band."""
    lic = [p for p in parts if p["license_state"] != "NONE"]
    keys = {p["key"] for p in parts}
    rows = [s for s in specs if s["participant_key"] in keys]
    started = [p for p in lic if p["spec_rows"] > 0]
    cand = Counter(p["legacy_band"] for p in started)
    crs = Counter()
    for s in rows:
        pct = 100.0 if s["completed"] else s["progress_pct"]
        if s["completed"]:
            crs["100%"] += 1
        elif pct > 50:
            crs[">50%"] += 1
        elif pct > 0:
            crs["<50%"] += 1
        else:
            crs["0%"] += 1
    gender = {g: {"invited": sum(1 for p in lic if p["gender"] == g), "started": sum(1 for p in started if p["gender"] == g)}
              for g in ("Male", "Female")}
    for g in gender.values():
        g["not_started"] = g["invited"] - g["started"]
    return {
        "invited": len(lic), "started": len(started), "not_started": len(lic) - len(started),
        "courses_registered": len(rows), "completed_candidates": cand["100%"], "courses_completed": crs["100%"],
        "gt50_candidates": cand[">50%"], "gt50_courses": crs[">50%"], "lt50_candidates": cand["<50%"], "lt50_courses": crs["<50%"],
        "zero_candidates": cand["0%"], "zero_courses": crs["0%"], "gender": gender,
    }


def legacy_group_table(parts, specs, key, order=None):
    groups = defaultdict(list)
    for p in parts:
        groups[p.get(key) or "Unassigned"].append(p)
    out = []
    for name, ps in groups.items():
        k = legacy_kpis(ps, specs)
        k["name"] = name
        out.append(k)
    if order:
        pos = {n: i for i, n in enumerate(order)}
        out.sort(key=lambda r: (pos.get(r["name"], 999), r["name"]))
    else:
        out.sort(key=lambda r: (-r["invited"], r["name"]))
    return out
