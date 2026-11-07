# GCC - Google Career Certificates Dashboard

Centralised dashboard & data-management system for the Google Career Certificates programme
(National -> Regional -> Local -> Area), built from the BRD.

## Where the files go
This zip is a **complete project**. Unzip it so that `manage.py` sits in your project root, next to the `gcc/` and `main/` folders:
```
your-repo/
  manage.py            <- here
  requirements.txt
  gcc/                 (settings.py, urls.py, wsgi.py, asgi.py)
  main/                (the app: models, views, templates, logic ...)
```
If you already have these files in your repo, copy over the new/changed ones and review with `git diff`.

## Run it
```bash
pip install -r requirements.txt
python manage.py makemigrations main      # first time only (creates main/migrations/0001_initial.py)
python manage.py migrate
python manage.py createsuperuser
python manage.py import_gcc "Master sheet - GCC 7th Sep 2026.xlsx" "Final list - 7th Sep 2026.xlsx" Coursera/*.csv
python manage.py runserver
```
Open http://127.0.0.1:8000/dashboard/ (the existing "coming soon" page at `/` is untouched).

## How it is used (day to day)
The dashboard is a **separate section of the website** (`/dashboard/`, its own login) - it is *not* inside the Django admin.

**Upload page** (`/dashboard/import/`, staff only). Pick what you are uploading from the dropdown:

| Dropdown choice | You upload | Template button |
|---|---|---|
| Everything (first time / full refresh) | all three boxes below | |
| Only the new Coursera export | **Step 3** - one ZIP per Coursera account (select both at once) | Download guide |
| Only the Final list | **Step 2** - the Final list workbook (.xlsx) | Download Final list template |
| Only the Master registration sheet | **Step 1** - the Google-Form export (.xlsx) | Download Master template |

* Anything you do **not** upload keeps using the file stored by the last successful import (kept in `data_store/`, which holds personal data: it is git-ignored and never served).
* The **first** import needs all three.
* Every file is checked **before** anything changes. Mistakes are explained in plain words (e.g. "This looks like the FINAL LIST - please upload it in Step 2") and nothing is changed.
* The Coursera ZIPs are unzipped safely (only CSV files are read; paths inside the zip are never trusted).
* The templates contain **headers only** (examples are on the READ ME sheet), so an untouched template can never import fake people.

The system then matches the three datasets (Coursera email -> CNIC), replaces the previous snapshot atomically and recomputes everything.
Chairs log in and see only their own region / council / area. National users also get **Data quality** (reconciliation with Coursera's totals, unmatched people, warnings).
The Django **admin** is only for set-up: creating users and their *User scope*, correcting what counts as a Professional Certificate (*Specializations*), and manual *Identity overrides*.
Guard-rails: a red banner appears on every page if an upload looks incomplete (missing Coursera account, no enrolment files, numbers not matching Coursera's totals), and an import is refused if it drops a Coursera account the previous import had (unless you tick "allow fewer accounts").
Command line: `python manage.py import_gcc <files...>` does the same (files are sorted into the three types by content).

## Who sees what
Create users in Admin, then **User scopes**: role + (region | local council | area).
* superuser / National Chairman / National Program Team with no region -> everything
* Regional Chair / ROM -> their region only; Local Chair -> their council; PO -> their area
* A user with no scope sees nothing. The filter bar can only *narrow* a user's scope, never widen it.

## Tests
```bash
python manage.py test                       # Django integration tests + business-rule tests
python -m unittest main.logic.tests_logic   # business rules only (no Django needed)
```

## Proof against the 2025 Excel dashboard
```bash
python manage.py verify_excel_parity "GCC Dashboard as of 5th Nov 2025 - National.xlsx"
```
Feeds the old workbook's own data through the engine: **18/18 national cards and 99/99 per-region cards** reproduce exactly
(candidate bands use the workbook's own rule in this mode). The dashboard's headline completion figures follow the BRD
(Professional Certificate only), and an "Excel-compatible view" shows the old definitions side by side.

## Where the logic lives (read `main/logic/`)
| file | purpose |
|---|---|
| `normalize.py` | CNIC / email / date cleaning (the join keys) |
| `catalog.py` | regions, **which specializations are Professional Certificates**, bands |
| `upload_check.py` / `templates_gen.py` / `datastore.py` | the upload page: friendly checks, downloadable templates, stored files |
| `pipeline.py` | identity resolution, dedupe, consolidation, reconciliation with Coursera totals |
| `metrics.py` | every KPI definition and chart aggregation |
| `excel_parity.py` | reproduces the 2025 Excel cards from its own data |
| `../reports.py` | the downloadable reports + follow-up lists |

Business-rule decisions are listed on the in-app **Methodology** page.

## Decision to confirm
`Google AI` (7/8-course "Google AI Professional Certificate") is classified as a **Professional Certificate**;
`AI Essentials`, `Prompting Essentials` etc. are **AI courses** (reported separately, not counted as completion).
To change this: Admin -> Specializations -> edit *category* -> Data quality -> *Recalculate*. No re-upload needed.
