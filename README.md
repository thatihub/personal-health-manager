# Personal Health Manager

Personal health workspace with:

- `index.html` - Home
- `dashboard.html` - Lab Results Dashboard (lab trends)
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
