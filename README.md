# Personal Health Manager

Personal health workspace with:

- `index.html` - Home
- `dashboard.html` - Lab Results Dashboard (lab trends)
- `bio-age.html` - Biological/Metabolic age dashboard (section scores + age delta)
- `lab-import.html` - Multi-lab import/review (paste CSV/text -> normalize -> save)
- `bp.html` - Blood Pressure Dashboard (last 90 days)
- `vault.html` - Health Vault (insurance links, contacts, scanned-doc index)

## v2 architecture (revamp, Sep 2026)

- `static/css/theme.css` — the single design system (dark-first, token-based).
  No per-page `<style>` blocks; per-page accent comes from `body[data-page]`,
  set by `static/js/shell.js` from its page registry.
- `static/js/shell.js` — shared top nav + storage pill + mobile bottom tab bar,
  injected on every page.
- `static/js/app.js` — shared fetch helpers, toasts, formatting, header badges.
- `static/js/charts.js` — shared SVG chart builders used by the chart pages.
- `run_dashboard_server.py` — thin entry: route dispatch only (~300 lines).
- `api/` — domain modules mixed into the request handler:
  `config.py` (paths, auth, env), `auth.py`, `core.py`, `labs.py`, `bp.py`,
  `weight.py`, `uploads.py`, `records.py`.
- All 17 pages + all API routes keep their exact URLs and behavior.

## Start locally

```bash
python3 /Users/prakashthatikunta/Documents/Health/personal_health_manager_muse/run_dashboard_server.py --port 3002
```

Open:

- `http://localhost:3002/index.html`

## Rebuild lab data

```bash
python3 /Users/prakashthatikunta/Documents/Health/personal_health_manager_muse/rebuild_consolidation.py
```

This regenerates:

- `consolidated_labs.csv`
- `consolidated_labs.json`
- `dashboard_data.js`

## AG / Biological Age APIs

- `GET /api/ag-dashboard-data` -> normalized timeline + section scores + age estimate snapshot
- `POST /api/ag-import/preview` -> parse/normalize pasted text or CSV rows and return review preview
- `POST /api/ag-import/commit` -> save reviewed normalized rows to `ag_lab_imports.json`

## Configure source folder (optional)

If your lab PDFs are somewhere else, set:

```bash
export LAB_RESULTS_DIR="/absolute/path/to/Lab Results"
```

Then run rebuild.

## Vault data

Edit `vault_data.json` to manage:

- insurance details
- important links
- phone contacts
- scanned document references

## Vault Basic Auth (recommended)

To protect the Vault page (`/vault.html`) and its data (`/vault_data.json`), set both env vars:

```bash
export VAULT_BASIC_AUTH_USER="your_username"
export VAULT_BASIC_AUTH_PASS="your_password"
```

If both are set, Vault requires HTTP Basic Auth.
If either is missing, Vault auth is disabled.

## Last Updated Banner

`dashboard.html` and `vault.html` now show a live "Last updated" badge from:

- `/api/last-updated`

## Environment-backed sensitive data (Render-ready)

You can keep sensitive data out of GitHub by loading data from env vars at runtime.

Supported env overrides:

- `VAULT_DATA_JSON` or `VAULT_DATA_JSON_B64` -> serves `/vault_data.json`
- `DASHBOARD_DATA_JSON` or `DASHBOARD_DATA_JSON_B64` -> serves `/dashboard_data.js`
- `CONSOLIDATED_LABS_JSON` or `CONSOLIDATED_LABS_JSON_B64` -> serves `/consolidated_labs.json`
- `DATA_LAST_UPDATED` (optional label for banner when env data is used)

Notes:

- If both plain and `_B64` are set, plain value is used.
- If env var is missing, server falls back to local file.
- `_B64` is recommended for large JSON values in Render.

### Prepare base64 values locally

```bash
cd "/Users/prakashthatikunta/Documents/Health/personal_health_manager_muse"
base64 < vault_data.json | tr -d '\n'
base64 < consolidated_labs.json | tr -d '\n'
```

For dashboard JSON array from `dashboard_data.js`:

```bash
cd "/Users/prakashthatikunta/Documents/Health/personal_health_manager_muse"
sed 's/^window.LAB_DASH_DATA = //; s/;[[:space:]]*$//' dashboard_data.js | base64 | tr -d '\n'
```

Set the generated strings in Render env vars:

- `VAULT_DATA_JSON_B64`
- `CONSOLIDATED_LABS_JSON_B64`
- `DASHBOARD_DATA_JSON_B64`

Then deploy.

## Admin Upload Page (easier updates)

Use `/admin.html` to upload updated data files from browser.

Security:

- Set admin credentials in Render:
  - `ADMIN_BASIC_AUTH_USER`
  - `ADMIN_BASIC_AUTH_PASS`
- `admin.html` and `/api/admin/*` require this Basic Auth.

Upload targets in Admin page:

- New Lab Result PDF -> uploads PDF to server folder and triggers rebuild
- Blood Pressure CSV -> appends non-duplicate BP rows to `bp_readings.csv`
- Vault JSON -> updates `vault_data.json`
- Dashboard JSON/JS -> updates `dashboard_data.js`
- Consolidated JSON -> updates `consolidated_labs.json`

Important:

- If env overrides (`*_JSON` or `*_JSON_B64`) are active for a target, admin upload for that target is blocked.
- Remove env override vars if you want file uploads to take effect.

PDF upload notes:

- Uploaded PDFs are stored under:
  - `ADMIN_UPLOADS_DIR` (if set), otherwise `/_uploaded_pdfs` under project folder.
- Rebuild uses `LAB_RESULTS_DIR` if set.
- If `LAB_RESULTS_DIR` is not set, rebuild automatically uses uploaded PDF folder when it exists.
- Duplicate PDF uploads are detected by file hash; duplicate files are skipped with "File already uploaded" message.

BP CSV notes:

- Upload from `/admin.html` -> "Upload BP CSV (Append)".
- Incoming CSV rows are merged into `bp_readings.csv`.
- Duplicate rows (same date, time, SYS, DIAS, Pulse) are skipped.
- `/bp.html` displays only the latest 90 days.

## Daily weight

Open `/weight.html` from Home using the running app server. New entries default to the
browser's current local date/time. Edit an existing row to change its time, date, or
weight. A second entry for the same entered calendar day is rejected, including when
moving an entry to an occupied day. Measurements use pounds.

`GET /api/weight-data` returns entries and health context. `POST /api/admin/weight-entry`
uses the existing admin authentication and atomic JSON-file writer. Entries are stored
in `weight_entries.json` (created only on first save), with stable `id`, local wall-clock
`measured_at` (`YYYY-MM-DDTHH:mm`, intentionally no timezone conversion), `weight_lb`,
and timezone-aware audit timestamps `created_at`/`updated_at`. Writes are serialized.
Invalid or unreadable existing data is not replaced. Existing scans and legacy home
weight remain intact; no fabricated daily entries are migrated.

The latest non-future daily measurement feeds current BMI (using recorded scan height)
and weight change. Without daily entries, the existing home reference, then scan weight,
is used. Current context is included in the biological dashboard API and displayed on
Daily Weight, Biological Age, and Body Composition. DEXA and InBody context is kept
separate, with scan dates and original notes. Historical composition and BMR are unchanged: weight alone cannot establish new
fat/muscle measurements or justify an additional biological-age adjustment. The
clinical PhenoAge replacement is documented below. Future-dated entries stay in history
but are excluded from current context using the server's local calendar date.

Run weight regressions: `python3 -m unittest discover -s tests -p 'test_weight*.py'`.

## Clinical PhenoAge (Levine 2018)

The biological-age API/page now uses the published clinical PhenoAge calculation,
replacing the custom score-to-years lookup. Existing lab wellness scores remain
visible but are explicitly labeled custom/educational and never feed the age output.
This is clinical PhenoAge, not the DNA-methylation version and not a diagnosis or
individual lifespan prediction. No individual accuracy/confidence percentage is claimed.

Sources:
- https://doi.org/10.18632/aging.101414 (Table 1 and Supplement 1)
- https://cdn.aging-us.com/article/101414/supplementary/SD1/0/aging-v10i4-101414-supplementary-material-SD1.pdf
- https://github.com/dayoonkwon/BioAge/blob/master/R/phenoage_calc.R (`orig=TRUE`, full-precision coefficients)

`phenoage.py` records coefficients and explicit unit conversions. The formula uses
albumin (g/L), creatinine (µmol/L), glucose (mmol/L), natural-log CRP (mg/dL),
lymphocyte percentage, MCV (fL), RDW-CV (%), alkaline phosphatase (U/L), WBC
(10³/µL), and age at collection. The final denominator is **0.090165**, as in the
original supplement and BioAge implementation. The calculation evaluates the
algebraically equivalent log-hazard expression to avoid probability rounding errors.

App data policy (a conservative application rule, not an additional validated model):
- Use one collection date and one lab source for all nine markers. Separate reports
  from that same date/source may contribute; conflicting duplicate measurements block
  the estimate. No cross-date assembly, substitutions, imputation, or invented ages.
- Show the most recent collection containing a required marker. For multiple sources
  on that date, use the source with most valid inputs (then source name for stable ties).
  Previous complete assessments appear separately, each with its collection date.
- Use the existing Vault birth date (including environment overrides) and completed
  years at collection; no default age. Require age 20+, matching the original adult
  population. Future-dated labs are excluded. No claim that old results describe today.
- Check original numeric text and original units. Reject unknown/missing units,
  nonfinite/negative values, censored results (< or >), zero CRP, absolute lymphocytes,
  RDW-SD, and conflicting values. CRP and hs-CRP measure the same analyte and accept
  mg/L or mg/dL. Conflicting same-day CRP/hs-CRP requires review.
- The displayed difference is PhenoAge minus age at collection, **not** the
  regression-residual PhenoAgeAccel measure used in some research.
- Weight/BMI and DEXA/InBody information stay separate. No fabricated age adjustments.

The API retains `estimated_bio_age` and `age_delta_years` as nullable compatibility
fields, with full details under `phenoage`. `model_version` identifies the new method.
Old cached `ag-v1` age estimates are never displayed as PhenoAge; use the running
server to refresh the static export. Original lab, scan, and weight records are not
rewritten. Missing inputs can be supplied through the existing Lab Import + Review.

Tests: `python3 -m unittest discover -s tests -p 'test_*.py'` and
`node tests/phenoage-view.cjs`.

## Durable hosted storage for weight and body scans

Render's default filesystem is ephemeral. The repository's existing free-plan
configuration does **not** preserve writes across restarts, deploys, or spin-downs.
See https://render.com/docs/disks and https://render.com/docs/free .

The app now disables scan/weight saves on Render until a real mounted disk is
configured. `/api/storage-status` reports whether these writes are enabled and the
Admin and Weight pages show a notice when they are disabled. Local file storage
continues to work without configuration. This does not make the existing free plan
durable; a hosting/account change is still required. No paid plan is enabled by code.

Setup in Render (requires a paid service and disk):
1. Back up any currently available records before a deploy/restart.
2. Attach a persistent disk with mount path `/var/data`.
3. Set `HEALTH_DATA_DIR=/var/data` for the service.
4. Restart the service. Existing repository scan/weight JSON is copied into the data
   folder only when the destination does not exist. Subsequent startups never replace
   the durable files with repository defaults. Restore previously exported records to
   the mounted folder before entering new data when those are newer than the seeds.

The data folder holds `weight_entries.json`, `dexa_records.json`, uploaded
`dexa_scans/`, and prior scan JSON versions in `record_backups/`. Scan reads and both
scan write paths use the same configured location. Individual scan updates preserve
unrelated dates and modalities and require an explicit DEXA/InBody type. Corrupt JSON
blocks saving; it is never interpreted as an empty history. These protections cover
weight and scan storage; other existing app files still use their previous locations.
A disk cannot recover changes that were already lost from ephemeral storage.

Tests: `python3 -m unittest discover -s tests -p 'test_*.py'` includes simulated
restart/deploy seeding, actual fresh-process persistence, scan save/load, same-day
modalities, corrupted-file preservation, backups, and hosted write protection.
