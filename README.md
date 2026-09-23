# Personal Health Manager

Personal health workspace with:

- `index.html` - Home
- `dashboard.html` - Lab Results Dashboard (lab trends)
- `bio-age.html` - Biological/Metabolic age dashboard (section scores + age delta)
- `lab-import.html` - Multi-lab import/review (paste CSV/text -> normalize -> save)
- `bp.html` - Blood Pressure Dashboard (last 90 days)
- `vault.html` - Health Vault (insurance links, contacts, scanned-doc index)

## Start locally

```bash
python3 /Users/prakashthatikunta/Documents/Health/personal_health_manager/run_dashboard_server.py --port 3002
```

Open:

- `http://localhost:3002/index.html`

## Rebuild lab data

```bash
python3 /Users/prakashthatikunta/Documents/Health/personal_health_manager/rebuild_consolidation.py
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
cd "/Users/prakashthatikunta/Documents/Health/personal_health_manager"
base64 < vault_data.json | tr -d '\n'
base64 < consolidated_labs.json | tr -d '\n'
```

For dashboard JSON array from `dashboard_data.js`:

```bash
cd "/Users/prakashthatikunta/Documents/Health/personal_health_manager"
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
separate, with scan dates and original notes. Historical composition, BMR, and age-score
formulas are unchanged: weight alone cannot establish new fat/muscle measurements or
justify an additional biological-age adjustment. Future-dated entries stay in history
but are excluded from current context using the server's local calendar date.

Run weight regressions: `python3 -m unittest discover -s tests -p 'test_weight*.py'`.
