"""Merge the three datasets into ONE consolidated record per participant (pure Python).

Dataset 1  Registration form (Master sheet)      -> registrations
Dataset 2  Shortlist ("Final list" workbook)      -> screened persons
Dataset 3  Coursera exports                       -> licences (Program Activity) + enrolments
                                                     (Specialisation Activity / Course Activity)

Identity resolution for a Coursera licence (BRD s.4: ID -> Email -> CNIC), first match wins:
  0. manual override table (account,email) -> CNIC   [admin-managed]
  1. licence email == a shortlisted person's course-access email
  2. Coursera 'External ID' (== CNIC) == a shortlisted person's CNIC
  3. Coursera 'External ID' == a registration CNIC   -> person created, flagged "licensed, not shortlisted"
  4. licence email == a registration email           -> idem
  5. nothing matches                                  -> orphan person, flagged for review
Names are NEVER used to merge, only to suggest candidates to a human reviewer.
Every ambiguity or conflict becomes an entry in `issues`; nothing is dropped silently.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from . import catalog as C
from . import metrics as M
from . import normalize as N

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"


@dataclass
class Sources:
    master_rows: list
    final_sheets: dict          # sheet name -> rows
    coursera: dict              # account -> {'program': [...], 'spec': [...], 'course': [...], 'summary': row|None}
    as_of: date
    overrides: dict = field(default_factory=dict)       # (account, email) -> cnic
    spec_overrides: dict = field(default_factory=dict)  # slug -> {'family':..,'category':..}
    inactivity_days: int = M.DEFAULT_INACTIVITY_DAYS


def build(src: Sources) -> dict:
    issues = []

    def issue(sev, code, msg, ref="", pkey=None, lkey=None):
        issues.append({"severity": sev, "code": code, "message": msg, "ref": ref, "participant_key": pkey, "license_key": lkey})

    # ================================================================= 1. registrations
    regs, reg_by_cnic, reg_by_email, seen_ids = [], {}, defaultdict(list), set()
    for row in src.master_rows:
        rid = N.parse_int(row.get("id"))
        if rid is None:
            issue(WARNING, "REG_NO_ID", "Registration row without an id was skipped.", ref=str(row.get("Full Name")))
            continue
        if rid in seen_ids:
            issue(ERROR, "REG_DUP_ID", f"Registration id {rid} appears more than once; later row skipped.", ref=str(rid))
            continue
        seen_ids.add(rid)
        raw_cnic = row.get("CNIC Number")
        cnic = N.norm_cnic(raw_cnic)
        if cnic is None:
            issue(WARNING, "REG_BAD_CNIC", f"Registration {rid} has a missing/invalid CNIC ({raw_cnic!r}).", ref=str(rid))
        e_course, e_main = N.norm_email(row.get("Email Address (Course Access)")), N.norm_email(row.get("Email Address"))
        dob = N.parse_dt(row.get("Date of Birth"))
        reported_age = N.parse_float(row.get("Age (completed years)"))
        age = C.compute_age(dob, reported_age, src.as_of)
        if age is None:
            issue(INFO, "REG_BAD_AGE", f"Registration {rid}: DOB/age not plausible (DOB={row.get('Date of Birth')!r}, age={reported_age}).", ref=str(rid))
        income = N.clean_text(row.get("Monthly family income"))
        pref_label = N.getcol(row, "Which certification would you like to en")
        reg = {
            "form_id": rid, "name": N.clean_text(row.get("Full Name")) or f"Registration {rid}",
            "email": e_main, "course_email": e_course, "mobile": N.clean_text(row.get("Mobile Number")),
            "cnic": cnic, "gender": N.title_gender(row.get("Gender")),
            "marital_status": N.clean_text(row.get("Marital Status")),
            "dob": dob.date() if dob else None, "age": age,
            "origin_region": N.clean_text(row.get("What is your place of origin?")),
            "currently_studying": N.clean_text(row.get("Currently Studying")),
            "education_level": N.clean_text(row.get("Education Level")),
            "profession": N.clean_text(row.get("Profession")),
            "occupation_type": N.clean_text(row.get("occupation_type")),
            "income_band": income if income in C.INCOME_BANDS else (income or C.INCOME_UNKNOWN),
            "residing_region": C.canon_region(row.get("Residing Region")),
            "local_council": N.clean_text(row.get("Current Local Council")),
            "area": N.clean_text(row.get("JK Attendance")),
            "preferred_label": N.clean_text(pref_label), "preferred_family": C.preferred_family(N.clean_text(pref_label)),
            "created_at": N.parse_dt(row.get("Created At")),
        }
        reg["prof_status"] = C.professional_status(reg["currently_studying"], reg["profession"], reg["occupation_type"])
        reg["education_group"] = C.education_group(reg["education_level"])
        reg["age_bracket"] = C.age_bracket(age)
        regs.append(reg)
    # indexes (deterministic: latest registration id wins a CNIC clash, clash is reported)
    for reg in sorted(regs, key=lambda r: r["form_id"]):
        if reg["cnic"]:
            if reg["cnic"] in reg_by_cnic:
                issue(WARNING, "REG_DUP_CNIC", f"CNIC registered twice (ids {reg_by_cnic[reg['cnic']]['form_id']} and {reg['form_id']}); latest used.", ref=reg["cnic"])
            reg_by_cnic[reg["cnic"]] = reg
        for em in {reg["course_email"], reg["email"]} - {None}:
            reg_by_email[em].append(reg)

    # ================================================================= 2. shortlist (Final list)
    entries = []
    for sheet, rows in src.final_sheets.items():
        sname = (sheet or "").strip()
        region = C.SHEET_REGION.get(sname)
        if region is None:
            issue(WARNING, "FINAL_UNKNOWN_SHEET", f"Final-list sheet '{sname}' is not mapped to a region and was skipped.", ref=sname)
            continue
        for r in rows:
            name = N.clean_text(r.get("Full Name"))
            if not name:
                continue
            status_note = N.clean_text(r.get("Issuance Status")) or N.clean_text(r.get("Status"))
            entries.append({
                "sheet": sname, "region": region, "name": name,
                "email": N.norm_email(r.get("Email Address (Course Access)")) or N.norm_email(r.get("Email Address")),
                "email_alt": N.norm_email(r.get("Email Address")),
                "cnic": N.norm_cnic(r.get("CNIC Number")), "raw_cnic": r.get("CNIC Number"),
                "gender": N.title_gender(r.get("Gender")),
                "preferred_label": N.clean_text(N.getcol(r, "Which certification would you like to en")),
                "note": status_note,
            })

    persons, email_index = {}, {}
    for e in entries:
        if e["cnic"] is None:
            issue(ERROR, "FINAL_BAD_CNIC", f"Shortlisted '{e['name']}' ({e['sheet']}) has a missing/invalid CNIC ({e['raw_cnic']!r}).", ref=e["name"])
            if not e["email"]:
                issue(ERROR, "FINAL_NO_IDENTITY", f"Shortlisted '{e['name']}' ({e['sheet']}) has no CNIC and no email; skipped.", ref=e["name"])
                continue
            key = "EMAIL:" + e["email"]
        else:
            key = e["cnic"]
        p = persons.get(key)
        if p is None:
            persons[key] = p = _new_person(key, e["cnic"], e["name"], e["gender"], e["region"], True)
            p["entries"] = [e]
        else:
            p["entries"].append(e)
            if e["email"] == p["entries"][0]["email"]:
                issue(INFO, "FINAL_DUP_ROW", f"'{e['name']}' is listed twice in the shortlist with identical CNIC and email; counted once.", ref=key, pkey=key)
            else:
                issue(WARNING, "FINAL_MULTI_EMAIL", f"'{e['name']}' has several shortlist rows with different emails for the same CNIC (one person = one licence).", ref=key, pkey=key)
            if e["region"] != p["entries"][0]["region"]:
                issue(WARNING, "FINAL_MULTI_REGION", f"'{e['name']}' is shortlisted in more than one region sheet.", ref=key, pkey=key)
        for em in (e["email"], e["email_alt"]):
            if not em:
                continue
            prior = email_index.get(em)
            if prior and prior != key:
                issue(ERROR, "FINAL_EMAIL_CONFLICT", f"Email {em} is used by two different shortlisted people.", ref=em)
            else:
                email_index[em] = key
            p["emails"].add(em)

    # link persons -> registration
    used_regs = {}
    for key, p in persons.items():
        reg, how = (reg_by_cnic.get(p["cnic"]) if p["cnic"] else None), "cnic"
        if reg is None:
            for em in sorted(p["emails"]):
                cands = reg_by_email.get(em, [])
                if len(cands) == 1:
                    reg, how = cands[0], "email"
                    break
        _attach_registration(p, reg, how, used_regs, issue)

    # ================================================================= 3. licences
    licenses, lic_by_key, lic_by_email = [], {}, defaultdict(list)

    def ensure_person_from_cnic(cnic, lic):
        if cnic in persons:
            return persons[cnic]
        reg = reg_by_cnic.get(cnic)
        p = _new_person(cnic, cnic, (reg or {}).get("name") or lic["name"], (reg or {}).get("gender"), None, False)
        p["entries"] = []
        persons[cnic] = p
        if reg is not None:
            _attach_registration(p, reg, "cnic", used_regs, issue)
        return p

    for acct in sorted(src.coursera):
        for r in src.coursera[acct].get("program", []):
            email = N.norm_email(r.get("Email"))
            if not email:
                issue(ERROR, "LICENSE_NO_EMAIL", f"A Coursera membership row in {acct} has no email; skipped.", ref=str(r.get("Name")))
                continue
            lkey = (acct, email)
            if lkey in lic_by_key:
                issue(WARNING, "LICENSE_DUP_ROW", f"{email} appears twice in the Program Activity export of {acct}; later row skipped.", ref=email)
                continue
            raw_state = (N.clean_text(r.get("Member State")) or "").upper()
            state = {"MEMBER": "ACCEPTED", "INVITED": "PENDING"}.get(raw_state, "OTHER")
            if state == "OTHER":
                issue(WARNING, "LICENSE_UNKNOWN_STATE", f"{email}: unknown Coursera member state '{raw_state}'.", ref=email)
            pname = N.clean_text(r.get("Program Name"))
            lic = {
                "account": acct, "email": email, "name": N.clean_text(r.get("Name")) or email,
                "ext_cnic": N.norm_cnic(r.get("External ID")), "program_name": pname,
                "program_region": C.region_from_program(pname), "program_gender": C.gender_from_program(pname),
                "member_state": raw_state, "state": state, "removed": False,
                "invited_at": N.parse_dt(r.get("Invitation Date")), "joined_at": N.parse_dt(r.get("Join Date")),
                "last_activity_at": N.parse_dt(r.get("Last Program Activity Date")),
                "enrolled_courses": N.parse_int(r.get("# Enrolled Courses"), 0),
                "completed_courses": N.parse_int(r.get("# Completed Courses"), 0),
                "learning_hours": N.parse_float(r.get("Learning Hours"), 0.0),
                "contract": N.clean_text(r.get("Contract(s)")),
            }
            if pname and lic["program_region"] is None:
                issue(WARNING, "LICENSE_UNKNOWN_REGION", f"Program '{pname}' could not be mapped to a region.", ref=email, lkey=lkey)
            # ---- identity resolution
            method, p = None, None
            ov = N.norm_cnic(src.overrides.get(lkey)) if src.overrides.get(lkey) else None
            if ov:
                p, method = ensure_person_from_cnic(ov, lic), "override"
            elif email in email_index:
                p, method = persons[email_index[email]], "email"
                if lic["ext_cnic"] and p["cnic"] and lic["ext_cnic"] != p["cnic"]:
                    issue(ERROR, "LICENSE_CNIC_CONFLICT",
                          f"{email}: Coursera External ID ({lic['ext_cnic']}) differs from the shortlisted CNIC ({p['cnic']}). Matched by email; please verify.",
                          ref=email, pkey=p["key"], lkey=lkey)
            elif lic["ext_cnic"] and lic["ext_cnic"] in persons:
                p, method = persons[lic["ext_cnic"]], "cnic"
                issue(INFO, "LICENSE_EMAIL_DIFFERS", f"Licence email {email} differs from the shortlist email for the same CNIC; matched by CNIC.", ref=email, pkey=p["key"], lkey=lkey)
            elif lic["ext_cnic"] and lic["ext_cnic"] in reg_by_cnic:
                p, method = ensure_person_from_cnic(lic["ext_cnic"], lic), "registration_cnic"
            elif len(reg_by_email.get(email, [])) == 1 and reg_by_email[email][0]["cnic"]:
                p, method = ensure_person_from_cnic(reg_by_email[email][0]["cnic"], lic), "registration_email"
            elif lic["ext_cnic"]:
                p, method = ensure_person_from_cnic(lic["ext_cnic"], lic), "coursera_cnic_only"
            else:
                key = "EMAIL:" + email
                if key not in persons:
                    persons[key] = _new_person(key, None, lic["name"], None, None, False)
                    persons[key]["entries"] = []
                p, method = persons[key], "orphan"
            lic["person_key"], lic["match_method"] = p["key"], method
            p["emails"].add(email)
            p["licenses"].append(lic)
            if method in ("registration_cnic", "registration_email", "coursera_cnic_only", "orphan", "override") and not p["entries"]:
                if method == "orphan":
                    issue(WARNING, "LICENSE_UNIDENTIFIED", f"{lic['name']} <{email}> holds a Coursera licence but matches nobody in the shortlist or registration data.", ref=email, pkey=p["key"], lkey=lkey)
                elif method != "override":
                    issue(WARNING, "LICENSE_NOT_SHORTLISTED", f"{lic['name']} <{email}> holds a Coursera licence but is not in the shortlist (matched via {method.replace('_', ' ')}).", ref=email, pkey=p["key"], lkey=lkey)
            licenses.append(lic)
            lic_by_key[lkey] = lic
            lic_by_email[email].append(lic)

    # per-person licence anomalies (Rule 1: one participant = one licence)
    for p in persons.values():
        if len(p["licenses"]) > 1:
            issue(WARNING, "MULTIPLE_LICENSES", f"{p['name']} holds {len(p['licenses'])} Coursera licences ({', '.join(l['email'] for l in p['licenses'])}). Counted once; one licence is wasted.", ref=p["key"], pkey=p["key"])
    for em, ls in lic_by_email.items():
        if len({l["account"] for l in ls}) > 1:
            issue(ERROR, "LICENSE_IN_TWO_ACCOUNTS", f"{em} holds licences in more than one Coursera account.", ref=em)

    # shortlisted but no licence: suggest same-name candidates among licensed-not-shortlisted persons
    unmatched_lic_by_name = defaultdict(list)
    for p in persons.values():
        if p["licenses"] and not p["entries"]:
            unmatched_lic_by_name[N.name_key(p["name"])].append(p)
    for p in persons.values():
        if p["entries"] and not p["licenses"]:
            hint = ""
            cands = unmatched_lic_by_name.get(N.name_key(p["name"]), [])
            if cands:
                hint = " Possible match (same name, NOT merged automatically): " + ", ".join(c["licenses"][0]["email"] for c in cands) + "."
            issue(WARNING, "SHORTLISTED_NO_LICENSE", f"{p['name']} is shortlisted but no Coursera licence was found.{hint}", ref=p["key"], pkey=p["key"])

    # ================================================================= 4. enrolments
    spec_groups = defaultdict(list)
    unknown_specs = {}
    for acct in sorted(src.coursera):
        for r in src.coursera[acct].get("spec", []):
            email = N.norm_email(r.get("Email"))
            lic = lic_by_key.get((acct, email)) or (lic_by_email.get(email) or [None])[0]
            if lic is None:
                issue(WARNING, "ENROLMENT_NO_LICENSE", f"Specialization enrolment for {email} has no matching licence; ignored.", ref=str(email))
                continue
            slug = (N.clean_text(r.get("Specialization Slug")) or N.clean_text(r.get("Specialization")) or "unknown").lower()
            name = N.clean_text(r.get("Specialization")) or slug
            family, category, known = C.classify_specialization(slug, name, src.spec_overrides)
            if not known:
                unknown_specs[slug] = (name, category)
            total = N.parse_int(r.get("# Courses in Specialization"), 0)
            done = N.parse_int(r.get("# Completed Courses"), 0)
            completed = N.is_yes(r.get("Completed"))
            if N.is_yes(r.get("Removed From Program")):
                lic["removed"] = True
            spec_groups[(lic["account"], lic["email"], slug)].append({
                "slug": slug, "name": name, "family": family, "category": category,
                "enrolled_at": N.parse_dt(r.get("Enrollment Time")),
                "last_activity_at": N.parse_dt(r.get("Last Specialization Activity Time")),
                "completed_courses": done, "total_courses": total, "completed": completed,
                "completed_at": N.parse_dt(r.get("Specialization Completion Time")),
                "certificate_url": N.clean_text(r.get("Specialization Certificate URL")),
                "program_name": N.clean_text(r.get("Program Name")),
            })
    for slug, (name, cat) in unknown_specs.items():
        issue(INFO, "UNCLASSIFIED_SPECIALIZATION", f"Specialization '{name}' ({slug}) is not in the catalogue; treated as '{C.CATEGORY_LABELS[cat]}' and NOT counted as a Professional Certificate. Review it in Admin > Specializations.", ref=slug)

    specs = []
    merged_versions = 0
    for (acct, email, slug), rows in spec_groups.items():
        lic = lic_by_key[(acct, email)]
        merged = _consolidate_spec(rows)
        merged_versions += len(rows) - 1
        merged.update({"license_key": (acct, email), "participant_key": lic["person_key"]})
        p = persons[lic["person_key"]]
        if merged["completed"] and merged["total_courses"] and merged["completed_courses"] < merged["total_courses"]:
            issue(WARNING, "COMPLETED_PARTIAL",
                  f"{p['name']}: Coursera marks '{merged['name']}' as Completed but only {merged['completed_courses']}/{merged['total_courses']} courses are recorded as done (credit for prior learning?). Counted as completed per Coursera; please verify the certificate.",
                  ref=merged.get("certificate_url") or p["key"], pkey=p["key"], lkey=(acct, email))
        if merged["completed"] and merged["completed_at"] and merged["enrolled_at"] and (merged["enrolled_at"] - merged["completed_at"]).days >= 1:
            issue(INFO, "COMPLETED_BEFORE_ENROLMENT", f"{p['name']}: '{merged['name']}' completion ({merged['completed_at'].date()}) pre-dates enrolment in this programme ({merged['enrolled_at'].date()}).", ref=p["key"], pkey=p["key"])
        specs.append(merged)

    course_map = {}
    for acct in sorted(src.coursera):
        for r in src.coursera[acct].get("course", []):
            email = N.norm_email(r.get("Email"))
            lic = lic_by_key.get((acct, email)) or (lic_by_email.get(email) or [None])[0]
            if lic is None:
                issue(WARNING, "ENROLMENT_NO_LICENSE", f"Course enrolment for {email} has no matching licence; ignored.", ref=str(email))
                continue
            if N.is_yes(r.get("Removed From Program")):
                lic["removed"] = True
            cid = N.clean_text(r.get("Course ID")) or N.clean_text(r.get("Course Slug")) or N.clean_text(r.get("Course"))
            c = {
                "license_key": (lic["account"], lic["email"]), "participant_key": lic["person_key"],
                "course_id": cid, "name": N.clean_text(r.get("Course")) or cid, "slug": N.clean_text(r.get("Course Slug")),
                "course_type": N.clean_text(r.get("Course Type")) or "Course",
                "enrolled_at": N.parse_dt(r.get("Enrollment Time")),
                "last_activity_at": N.parse_dt(r.get("Last Course Activity Time")),
                "progress": N.parse_float(r.get("Overall Progress"), 0.0),
                "completed": N.is_yes(r.get("Completed")),
                "completed_at": N.parse_dt(r.get("Completion Time")),
                "grade": N.parse_float(r.get("Course Grade")),
                "hours": N.parse_float(r.get("Learning Hours"), 0.0),
            }
            k = (c["license_key"], cid)
            prev = course_map.get(k)
            if prev is None or (c["completed"], c["progress"]) > (prev["completed"], prev["progress"]):
                if prev is not None:
                    issue(INFO, "COURSE_DUP_ROW", f"Course '{c['name']}' appears twice for {lic['email']}; best row kept.", ref=lic["email"])
                course_map[k] = c
    courses = list(course_map.values())

    # ================================================================= 5. participants (+derived)
    specs_by_p, courses_by_p = defaultdict(list), defaultdict(list)
    for s in specs:
        specs_by_p[s["participant_key"]].append(s)
    for c in courses:
        courses_by_p[c["participant_key"]].append(c)

    participants = []
    for key, p in persons.items():
        reg, ent0 = p.get("reg"), (p["entries"][0] if p["entries"] else None)
        lics = p["licenses"]
        prog_regions = [l["program_region"] for l in lics if l["program_region"]]
        sheet_region = ent0["region"] if ent0 else None
        programme = prog_regions[0] if prog_regions else sheet_region
        if prog_regions and sheet_region and prog_regions[0] != sheet_region:
            issue(WARNING, "REGION_MISMATCH", f"{p['name']}: shortlist sheet region ({sheet_region}) differs from Coursera programme ({prog_regions[0]}). Coursera programme used.", ref=key, pkey=key)
        residing = (reg or {}).get("residing_region")
        # REGION = where the person lives (registration form), exactly as in the 2025 Excel and BRD s.5; the Region >
        # Local council > Area hierarchy is residence-based. Fallback when there is no registration: the Coursera
        # programme region (never 'National': National Council staff live in a region).
        region = residing or (programme if programme != "National" else None)
        if residing and programme and programme != "National" and residing != programme:
            issue(WARNING, "RESIDENCE_PROGRAMME_MISMATCH", f"{p['name']}: lives in {residing} but holds a {programme} programme licence. Reported under {residing} (residence).", ref=key, pkey=key)
        gender = (reg or {}).get("gender") or (ent0 or {}).get("gender") or next((l["program_gender"] for l in lics if l["program_gender"]), None) or p.get("gender")
        derived = M.derive_participant(
            licenses=[{k: l[k] for k in ("state", "removed", "invited_at", "joined_at", "last_activity_at", "learning_hours")} for l in lics],
            specs=specs_by_p.get(key, []), courses=courses_by_p.get(key, []),
            as_of=src.as_of, inactivity_days=src.inactivity_days)
        best_lic = sorted(lics, key=lambda l: (l["state"] != "ACCEPTED", l["email"]))[0] if lics else None
        participants.append({
            "key": key, "public_id": N.stable_id(key), "cnic": p["cnic"], "name": p["name"],
            "email": (best_lic["email"] if best_lic else (ent0["email"] if ent0 else (reg or {}).get("course_email"))),
            "phone": (reg or {}).get("mobile"), "gender": gender, "region": region, "programme_region": programme,
            "residing_region": residing,
            "local_council": (reg or {}).get("local_council"), "area": (reg or {}).get("area"),
            "shortlisted": bool(p["entries"]), "shortlist_sheet": ent0["sheet"] if ent0 else None,
            "issuance_note": next((e["note"] for e in p["entries"] if e["note"]), None),
            "preferred_label": (reg or {}).get("preferred_label") or (ent0 or {}).get("preferred_label"),
            "preferred_family": C.preferred_family((reg or {}).get("preferred_label") or (ent0 or {}).get("preferred_label")),
            "prof_status": (reg or {}).get("prof_status") or "Data not available",
            "education_group": (reg or {}).get("education_group") or "Data not available",
            "age_bracket": (reg or {}).get("age_bracket") or "Data not available",
            "income_band": (reg or {}).get("income_band") or C.INCOME_UNKNOWN,
            "registration_form_id": reg["form_id"] if reg else None,
            "coursera_external_id": next((l["ext_cnic"] for l in lics if l["ext_cnic"]), p["cnic"]),
            "accounts": sorted({l["account"] for l in lics}),
            **derived,
        })
    # registrations: region = residing region (so regions always add up to the grand total); programme from the linked participant
    prog_by_reg = {p["registration_form_id"]: p["programme_region"] for p in participants if p["registration_form_id"] is not None}
    for r in regs:
        r["region"] = r["residing_region"]
        r["programme_region"] = prog_by_reg.get(r["form_id"])

    recon = reconcile(src)
    alerts = _alerts(src, persons, recon, issue)
    stats = {
        "registrations": len(regs), "shortlist_rows": len(entries),
        "persons": len(persons), "participants_licensed": sum(1 for p in participants if p["license_state"] != "NONE"),
        "licenses": len(licenses), "spec_enrolments": len(specs), "spec_versions_merged": merged_versions,
        "course_enrolments": len(courses),
        "issues": dict(Counter(i["severity"] for i in issues)),
        "accounts": sorted(src.coursera), "alerts": alerts,
    }
    return {"registrations": regs, "participants": participants, "licenses": licenses, "specs": specs,
            "courses": courses, "issues": issues, "recon": recon, "stats": stats, "as_of": src.as_of}


# ---------------------------------------------------------------------------------- guard-rails
def _alerts(src, persons, recon, issue):
    """Loud warnings for the situations that would make the dashboard silently WRONG (partial uploads)."""
    alerts = []
    shortlisted = [p for p in persons.values() if p["entries"]]
    no_lic = [p for p in shortlisted if not p["licenses"]]
    if shortlisted and len(no_lic) / len(shortlisted) >= 0.05:
        msg = (f"{len(no_lic)} of {len(shortlisted)} shortlisted candidates have no Coursera licence. "
               "A Coursera account export may be missing from this upload - the numbers are NOT reliable.")
        alerts.append(msg)
        issue(ERROR, "POSSIBLE_MISSING_EXPORT", msg)
    for acct, d in sorted(src.coursera.items()):
        if d.get("program") and not d.get("spec") and not d.get("course"):
            msg = f"Coursera account {acct} has licences but no Specialisation/Course Activity file: everyone in it shows as 'not started'."
            alerts.append(msg)
            issue(ERROR, "ACCOUNT_NO_ENROLMENTS", msg, ref=acct)
        elif d.get("program") and not d.get("spec"):
            issue(WARNING, "ACCOUNT_NO_SPECIALIZATIONS", f"Coursera account {acct} has no Specialisation Activity file: Professional Certificate progress is unavailable.", ref=acct)
    bad = [c for c in recon if c.get("ok") is False]
    if bad:
        msg = f"{len(bad)} figure(s) do not match Coursera's own summary totals - see Data quality."
        alerts.append(msg)
        issue(ERROR, "RECONCILIATION_FAILED", msg)
    return alerts


# ---------------------------------------------------------------------------------- helpers
def _new_person(key, cnic, name, gender, region, shortlisted):
    return {"key": key, "cnic": cnic, "name": name, "gender": gender, "region": region, "shortlisted": shortlisted,
            "entries": [], "emails": set(), "licenses": [], "reg": None}


def _attach_registration(p, reg, how, used_regs, issue):
    if reg is None:
        if p["entries"]:
            issue(WARNING, "SHORTLIST_NO_REGISTRATION", f"{p['name']} is shortlisted but has no registration record (matched neither by CNIC nor email).", ref=p["key"], pkey=p["key"])
        return
    owner = used_regs.get(reg["form_id"])
    if owner and owner != p["key"]:
        issue(WARNING, "REGISTRATION_SHARED", f"Registration {reg['form_id']} matches two different people ({owner} and {p['key']}); linked only to the first.", ref=str(reg["form_id"]))
        return
    used_regs[reg["form_id"]] = p["key"]
    p["reg"] = reg
    if how == "email" and p["cnic"] and reg["cnic"] and reg["cnic"] != p["cnic"]:
        issue(WARNING, "CNIC_MISMATCH_REG", f"{p['name']}: shortlist CNIC {p['cnic']} differs from registration CNIC {reg['cnic']} (linked by email).", ref=p["key"], pkey=p["key"])


def _consolidate_spec(rows):
    """Several versions of the same specialization for one licence (e.g. 7- and 8-course editions)
    collapse into ONE enrolment: completed if any version completed; otherwise the most advanced version."""
    def score(r):
        ratio = (r["completed_courses"] / r["total_courses"]) if r["total_courses"] else 0.0
        return (bool(r["completed"]), ratio, N_ts(r["last_activity_at"]))
    best = max(rows, key=score)
    out = dict(best)
    out["completed"] = any(r["completed"] for r in rows)
    comp_rows = [r for r in rows if r["completed"]]
    if comp_rows:
        top = max(comp_rows, key=score)
        out["completed_courses"], out["total_courses"] = top["completed_courses"], top["total_courses"]
        out["completed_at"] = min((r["completed_at"] for r in comp_rows if r["completed_at"]), default=None)
        out["certificate_url"] = next((r["certificate_url"] for r in comp_rows if r["certificate_url"]), None)
    enrolled = [r["enrolled_at"] for r in rows if r["enrolled_at"]]
    acts = [r["last_activity_at"] for r in rows if r["last_activity_at"]]
    out["enrolled_at"] = min(enrolled) if enrolled else None
    out["last_activity_at"] = max(acts) if acts else None
    if out["completed"]:
        out["progress_pct"] = 100.0
    else:
        out["progress_pct"] = round(min(100.0, out["completed_courses"] * 100.0 / out["total_courses"]), 2) if out["total_courses"] else 0.0
    out["versions"] = len(rows)
    return out


def N_ts(dt):
    return dt.timestamp() if dt else float("-inf")


def reconcile(src: Sources):
    """Compare numbers recomputed from the raw rows with Coursera's own summary export (Activity_By_Business_Unit)."""
    checks = []
    for acct in sorted(src.coursera):
        d = src.coursera[acct]
        summ = d.get("summary")
        prog, course = d.get("program", []), d.get("course", [])
        members = sum(1 for r in prog if (N.clean_text(r.get("Member State")) or "").upper() == "MEMBER")
        learners = len({N.norm_email(r.get("Email")) for r in course if N.norm_email(r.get("Email"))})
        enrol = len(course)
        progs = [N.parse_float(r.get("Overall Progress"), 0.0) for r in course]
        avg = round(sum(progs) / len(progs), 2) if progs else 0.0
        done = sum(1 for r in course if N.is_yes(r.get("Completed")))
        if not summ:
            checks.append({"account": acct, "label": "Coursera summary export not supplied", "expected": None, "actual": None, "ok": None})
            continue
        for label, exp, act, tol in (
            ("Current members (accepted licences)", N.parse_float(summ.get("Current Members")), members, 0),
            ("Current learners (>=1 course enrolment)", N.parse_float(summ.get("Current Learners")), learners, 0),
            ("Total course enrolments", N.parse_float(summ.get("Total Enrollments")), enrol, 0),
            ("Average course progress %", N.parse_float(summ.get("Average Progress")), avg, 0.01),
            ("Total completed courses", N.parse_float(summ.get("Total Completed Courses")), done, 0),
        ):
            checks.append({"account": acct, "label": label, "expected": exp, "actual": act,
                           "ok": exp is not None and abs(exp - act) <= tol})
    return checks
