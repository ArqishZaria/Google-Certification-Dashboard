"""Django persistence for the pure pipeline: import, recompute, and scoped data access."""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from .logic import catalog as C
from .logic import loader, metrics as M, pipeline as P
from .models import (Area, CourseEnrollment, DataImport, DataIssue, IdentityOverride, License, LocalCouncil,
                     Participant, Region, Registration, SourceFile, Specialization, SpecializationEnrollment, UserScope)

INACTIVITY_DAYS = getattr(settings, "GCC_INACTIVITY_DAYS", M.DEFAULT_INACTIVITY_DAYS)

DERIVED_FIELDS = [
    "license_state", "license_count", "started", "status", "activity", "pc_progress", "pc_family", "pc_completed",
    "pc_completed_at", "pc_completed_families", "pc_enrolled", "ai_enrolled", "ai_completed", "short_enrolled",
    "short_completed", "specs_enrolled", "courses_enrolled", "courses_completed", "learning_hours", "last_activity",
    "first_enrolled_at", "invited_at", "joined_at", "days_inactive", "progress_band", "fine_band",
    "spec_rows", "spec_rows_done", "best_spec_pct", "legacy_band",
]


def ensure_regions():
    out = {}
    for i, name in enumerate(C.REGION_ORDER):
        r, _ = Region.objects.get_or_create(name=name, defaults={"sort_order": i, "is_national": name == "National"})
        out[name] = r
    return out


def db_overrides():
    identity = {(o.account, o.email.lower()): o.cnic for o in IdentityOverride.objects.all()}
    spec = {s.slug: {"family": s.family, "category": s.category} for s in Specialization.objects.all()}
    return identity, spec


def clear_snapshot():
    """Empty the computed snapshot (catalogue, geography, users and overrides are kept)."""
    for m in (DataIssue, CourseEnrollment, SpecializationEnrollment, License, Participant, Registration, DataImport):
        m.objects.all().delete()


def _rebuild_from_store(user=None, allow_fewer_accounts=False):
    """Recompute the dashboard from the files currently stored. Returns the DataImport, or None when the
    required files are not all loaded (in which case the snapshot is cleared so nothing stale is shown)."""
    import os
    import tempfile
    files = list(SourceFile.objects.all())
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, f in enumerate(files):
            d = os.path.join(tmp, str(i))
            os.makedirs(d)
            path = os.path.join(d, f.name)
            with open(path, "wb") as out:
                out.write(bytes(f.content))
            paths.append(path)
        try:
            return run_import(paths, user, allow_fewer_accounts=allow_fewer_accounts)
        except loader.MissingFilesError:
            clear_snapshot()
            return None


@transaction.atomic
def add_source_files(paths_and_names, user=None, allow_fewer_accounts=False):
    """Store uploaded files (replacing any earlier file of the same kind+account), then rebuild.
    -> (imp_or_None, stored, skipped). Any failure rolls back BOTH the stored files and the snapshot."""
    import os
    stored, skipped = [], []
    for path, name in paths_and_names:
        info = loader.classify_path(path)
        info["name"] = name
        if info["kind"] == "ignored":
            skipped.append((name, info["description"]))
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        SourceFile.objects.update_or_create(
            kind=info["kind"], account=info["account"],
            defaults=dict(name=name, file_date=info["file_date"], description=info["description"], size=len(data),
                          content=data, uploaded_by=user))
        stored.append(name)
    if not stored:
        return None, stored, skipped
    return _rebuild_from_store(user, allow_fewer_accounts), stored, skipped


@transaction.atomic
def remove_source_file(pk, user=None):
    """Delete one stored file and rebuild from what is left (an explicit removal may drop a Coursera account)."""
    f = SourceFile.objects.get(pk=pk)
    f.delete()
    return f, _rebuild_from_store(user, allow_fewer_accounts=True)


@transaction.atomic
def run_import(paths, user=None, allow_fewer_accounts=False):
    """Full, idempotent refresh from the supplied files. Either everything is replaced or nothing is."""
    identity, spec_over = db_overrides()
    src, detected = loader.load_sources(paths, identity, spec_over, INACTIVITY_DAYS)
    ds = P.build(src)
    prev = DataImport.objects.first()
    if prev and not allow_fewer_accounts:
        missing = sorted(set(prev.stats.get("accounts", [])) - set(ds["stats"]["accounts"]))
        if missing:
            raise ValueError("The previous import contained Coursera account(s) " + ", ".join(missing) +
                             " that are missing from these files. Upload every account's exports (or tick 'allow fewer accounts').")
    regions = ensure_regions()

    # ---- catalogue
    spec_obj = {s.slug: s for s in Specialization.objects.all()}
    for s in ds["specs"]:
        if s["slug"] not in spec_obj:
            _, _, known = C.classify_specialization(s["slug"], s["name"])
            spec_obj[s["slug"]] = Specialization.objects.create(
                slug=s["slug"], name=s["name"], family=s["family"], category=s["category"], reviewed=known)

    # ---- wipe previous snapshot (catalogue, regions, geography, users, overrides are kept)
    DataIssue.objects.all().delete()
    CourseEnrollment.objects.all().delete()
    SpecializationEnrollment.objects.all().delete()
    License.objects.all().delete()
    Participant.objects.all().delete()
    Registration.objects.all().delete()

    # ---- geography (get-or-create, case-insensitive)
    councils, areas = {}, {}
    for c in LocalCouncil.objects.select_related("region"):
        councils[(c.region_id, c.name.casefold())] = c
    for a in Area.objects.all():
        areas[(a.local_council_id, a.name.casefold())] = a

    def geo(reg):
        rr = regions.get(reg["residing_region"]) if reg["residing_region"] else None
        lc = ar = None
        if rr and reg["local_council"]:
            k = (rr.id, reg["local_council"].casefold())
            lc = councils.get(k) or councils.setdefault(k, LocalCouncil.objects.create(region=rr, name=reg["local_council"]))
            if reg["area"]:
                ak = (lc.id, reg["area"].casefold())
                ar = areas.get(ak) or areas.setdefault(ak, Area.objects.create(local_council=lc, name=reg["area"].title() if reg["area"].isupper() else reg["area"]))
        return rr, lc, ar

    reg_objs, geo_by_form = [], {}
    for r in ds["registrations"]:
        rr, lc, ar = geo(r)
        geo_by_form[r["form_id"]] = (lc, ar)
        reg_objs.append(Registration(
            form_id=r["form_id"], name=r["name"], email=r["email"], course_email=r["course_email"], mobile=r["mobile"],
            cnic=r["cnic"], gender=r["gender"], marital_status=r["marital_status"], dob=r["dob"], age=r["age"],
            origin_region=r["origin_region"], currently_studying=r["currently_studying"], education_level=r["education_level"],
            profession=r["profession"], occupation_type=r["occupation_type"], income_band=r["income_band"],
            preferred_label=r["preferred_label"], preferred_family=r["preferred_family"], prof_status=r["prof_status"],
            education_group=r["education_group"], age_bracket=r["age_bracket"], created_at=r["created_at"],
            programme_region=r["programme_region"],
            region=regions.get(r["region"]) if r["region"] else None, residing_region=rr, local_council=lc, area=ar))
    Registration.objects.bulk_create(reg_objs, batch_size=500)
    reg_id = dict(Registration.objects.values_list("form_id", "id"))

    part_objs = []
    for p in ds["participants"]:
        lc, ar = geo_by_form.get(p["registration_form_id"], (None, None))
        part_objs.append(Participant(
            key=p["key"], public_id=p["public_id"], cnic=p["cnic"], name=p["name"], email=p["email"], phone=p["phone"],
            gender=p["gender"], coursera_external_id=p["coursera_external_id"],
            registration_id=reg_id.get(p["registration_form_id"]),
            region=regions.get(p["region"]) if p["region"] else None, programme_region=p["programme_region"],
            residing_region=regions.get(p["residing_region"]) if p["residing_region"] else None,
            local_council=lc, area=ar, shortlisted=p["shortlisted"], shortlist_sheet=p["shortlist_sheet"],
            issuance_note=p["issuance_note"], preferred_label=p["preferred_label"], preferred_family=p["preferred_family"],
            prof_status=p["prof_status"], education_group=p["education_group"], age_bracket=p["age_bracket"],
            income_band=p["income_band"], accounts=p["accounts"], **{f: p[f] for f in DERIVED_FIELDS}))
    Participant.objects.bulk_create(part_objs, batch_size=500)
    pid = dict(Participant.objects.values_list("key", "id"))

    License.objects.bulk_create([
        License(participant_id=pid[l["person_key"]], account=l["account"], email=l["email"], name=l["name"], ext_cnic=l["ext_cnic"],
                program_name=l["program_name"], program_region=l["program_region"], member_state=l["member_state"], state=l["state"],
                removed=l["removed"], invited_at=l["invited_at"], joined_at=l["joined_at"], last_activity_at=l["last_activity_at"],
                enrolled_courses=l["enrolled_courses"], completed_courses=l["completed_courses"], learning_hours=l["learning_hours"],
                contract=l["contract"], match_method=l["match_method"]) for l in ds["licenses"]], batch_size=500)
    lid = {(a, e): i for i, a, e in License.objects.values_list("id", "account", "email")}

    SpecializationEnrollment.objects.bulk_create([
        SpecializationEnrollment(
            license_id=lid[s["license_key"]], participant_id=pid[s["participant_key"]], specialization=spec_obj[s["slug"]],
            enrolled_at=s["enrolled_at"], last_activity_at=s["last_activity_at"], completed_courses=s["completed_courses"],
            total_courses=s["total_courses"], progress_pct=s["progress_pct"], completed=s["completed"],
            completed_at=s["completed_at"], certificate_url=s["certificate_url"], versions=s["versions"]) for s in ds["specs"]], batch_size=500)
    CourseEnrollment.objects.bulk_create([
        CourseEnrollment(
            license_id=lid[c["license_key"]], participant_id=pid[c["participant_key"]], course_id=c["course_id"] or "", name=c["name"] or "",
            slug=c["slug"], course_type=c["course_type"], enrolled_at=c["enrolled_at"], last_activity_at=c["last_activity_at"],
            progress=c["progress"], completed=c["completed"], completed_at=c["completed_at"], grade=c["grade"], hours=c["hours"])
        for c in ds["courses"]], batch_size=500)

    imp = DataImport.objects.create(as_of=ds["as_of"], files=[f"{n} - {d}" for n, d in detected], stats=ds["stats"],
                                    recon=ds["recon"], inactivity_days=INACTIVITY_DAYS, created_by=user)
    DataIssue.objects.bulk_create([
        DataIssue(data_import=imp, severity=i["severity"], code=i["code"], message=i["message"], ref=(i["ref"] or "")[:200],
                  participant_id=pid.get(i["participant_key"])) for i in ds["issues"]], batch_size=500)
    return imp


@transaction.atomic
def recompute_all():
    """Re-derive every participant's status/progress from the stored enrolments (e.g. after changing a
    specialization's category in Admin). Uses exactly the same rules as the importer."""
    imp = DataImport.objects.first()
    if imp is None:
        return 0
    as_of = imp.as_of
    lic_by_p, spec_by_p, course_by_p = {}, {}, {}
    for l in License.objects.all():
        lic_by_p.setdefault(l.participant_id, []).append({
            "state": l.state, "removed": l.removed, "invited_at": l.invited_at, "joined_at": l.joined_at,
            "last_activity_at": l.last_activity_at, "learning_hours": l.learning_hours})
    for s in SpecializationEnrollment.objects.select_related("specialization"):
        spec_by_p.setdefault(s.participant_id, []).append({
            "family": s.specialization.family, "category": s.specialization.category, "progress_pct": s.progress_pct,
            "completed": s.completed, "completed_at": s.completed_at, "enrolled_at": s.enrolled_at, "last_activity_at": s.last_activity_at})
    for c in CourseEnrollment.objects.all():
        course_by_p.setdefault(c.participant_id, []).append({
            "completed": c.completed, "completed_at": c.completed_at, "enrolled_at": c.enrolled_at, "last_activity_at": c.last_activity_at})
    batch = []
    for p in Participant.objects.all():
        d = M.derive_participant(lic_by_p.get(p.id, []), spec_by_p.get(p.id, []), course_by_p.get(p.id, []), as_of, imp.inactivity_days)
        for f in DERIVED_FIELDS:
            setattr(p, f, d[f])
        batch.append(p)
    Participant.objects.bulk_update(batch, DERIVED_FIELDS, batch_size=500)
    return len(batch)


# ------------------------------------------------------------------ access control
def scope_q(user):
    """Return (Q, label, level) restricting Participant/Registration rows to what `user` may see, or None = no access.
    Participant and Registration share the field names region / local_council / area, so ONE Q serves both."""
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return Q(), "National", "national"
    s = getattr(user, "gcc_scope", None)
    if s is None:
        return None
    if s.role in UserScope.NATIONAL_ROLES and not (s.region_id or s.local_council_id or s.area_id):
        return Q(), "National", "national"
    if s.area_id:
        return Q(area_id=s.area_id), f"Area: {s.area}", "area"
    if s.local_council_id:
        return Q(local_council_id=s.local_council_id), f"Local council: {s.local_council.name}", "council"
    if s.region_id:
        return Q(region_id=s.region_id), f"Region: {s.region.name}", "region"
    return None   # a regional/local role without a region/council assigned sees nothing


def requested_q(params):
    """Optional narrowing from the filter bar; always ANDed with the user's scope so it can only narrow."""
    q = Q()
    if params.get("region"):
        q &= Q(region__name=params["region"])
    if params.get("council", "").isdigit():
        q &= Q(local_council_id=int(params["council"]))
    if params.get("area", "").isdigit():
        q &= Q(area_id=int(params["area"]))
    if params.get("programme") == "national":
        q &= Q(programme_region="National")
    elif params.get("programme") == "regional":
        q &= ~Q(programme_region="National")
    return q


def effective(user, params):
    sc = scope_q(user)
    if sc is None:
        return None
    base, label, level = sc
    req = requested_q(params)
    if params.get("area", "").isdigit():
        group_key = "area_label"
    elif params.get("council", "").isdigit() or level in ("council", "area"):
        group_key = "area_label"
    elif params.get("region") or level == "region":
        group_key = "council_label"
    else:
        group_key = "region"
    return {"q": base & req, "label": label, "level": level, "group_key": group_key}


PART_FIELDS = [
    "id", "key", "public_id", "cnic", "name", "email", "phone", "gender", "coursera_external_id", "shortlisted",
    "shortlist_sheet", "issuance_note", "preferred_label", "preferred_family", "prof_status", "education_group",
    "age_bracket", "income_band", "accounts", "programme_region",
] + DERIVED_FIELDS


def participant_dicts(qs):
    rows = list(qs.values(*PART_FIELDS, "region__name", "residing_region__name", "local_council__name", "area__name",
                          "local_council__region__name", "registration__form_id"))
    for r in rows:
        r["council_region"] = r.pop("local_council__region__name")
        r["region"] = r.pop("region__name")
        r["residing_region"] = r.pop("residing_region__name")
        r["local_council"] = r.pop("local_council__name")
        r["area"] = r.pop("area__name")
        r["registration_form_id"] = r.pop("registration__form_id")
    return M.add_geo_labels(rows)


def registration_dicts(qs):
    rows = list(qs.values("form_id", "created_at", "gender", "programme_region", "region__name", "local_council__name", "area__name",
                          "local_council__region__name"))
    for r in rows:
        r["council_region"] = r.pop("local_council__region__name")
        r["region"] = r.pop("region__name")
        r["local_council"] = r.pop("local_council__name")
        r["area"] = r.pop("area__name")
    return M.add_geo_labels(rows)


def spec_dicts(participant_qs):
    rows = list(SpecializationEnrollment.objects.filter(participant_id__in=participant_qs.values("id")).values(
        "participant__key", "specialization__family", "specialization__category", "specialization__name",
        "progress_pct", "completed", "completed_at", "enrolled_at", "completed_courses", "total_courses"))
    return [{
        "participant_key": r["participant__key"], "family": r["specialization__family"], "category": r["specialization__category"],
        "name": r["specialization__name"], "progress_pct": r["progress_pct"], "completed": r["completed"],
        "completed_at": r["completed_at"], "enrolled_at": r["enrolled_at"],
    } for r in rows]


def load_scope_data(eff, extra_q=None):
    """-> (participants, registrations, spec rows, as_of) for the effective scope."""
    pq = eff["q"] & (extra_q or Q())
    parts_qs = Participant.objects.filter(pq)
    parts = participant_dicts(parts_qs)
    regs = registration_dicts(Registration.objects.filter(eff["q"]))
    specs = M.enrich_specs(spec_dicts(parts_qs), parts)
    imp = DataImport.objects.first()
    return parts, regs, specs, (imp.as_of if imp else None)
