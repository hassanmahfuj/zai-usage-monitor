# Z.ai Usage Monitor

A Streamlit dashboard for visualizing Z.ai API token usage, cost, and request volume, with manual sync against the Z.ai billing API.

## Features

- **Manual Sync** — Fetch billing records from Z.ai for any date range with a single click. Supports pagination, retry on transient errors, and idempotent re-sync (duplicate records are safely ignored).
- **Interactive Filters** — Filter by date range, model, and user. Group charts by Date, Model, or Username.
- **KPI Cards** — At-a-glance totals for tokens, cost, and requests.
- **Cost Calculation** — Actual cost is computed as `cost_price * token_usage / 1000` since `cost_price` is a per-kToken rate.
- **Token Usage Chart** — Stacked bar showing INPUT, OUTPUT, and CACHE token breakdown.
- **Cost Chart** — Bar chart of cost by the selected group-by dimension.
- **Requests Chart** — Bar chart of request counts.
- **Raw Data Table** — Expandable table for spot-checking individual records.
- **User Mapping** — Admin seed script maps masked API keys to human-readable usernames for the UI.

## Setup

### 1. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

Edit `.streamlit/secrets.toml` with your Z.ai customer ID and bearer token:

```toml
[zai]
customer_id = "YOUR_CUSTOMER_ID"
bearer_token = "YOUR_BEARER_TOKEN"
```

### 3. Seed API key mappings

Open `seed_api_keys.py` and fill in the three config values at the top (`ORG_ID`, `PROJECT_ID`, `BEARER_TOKEN`), then run:

```bash
python seed_api_keys.py              # fetch and insert into the database
python seed_api_keys.py --dry-run    # preview without inserting
```

Re-running is safe — duplicate API keys are ignored. The script skips hidden keys and maps each user's name to their masked API key automatically.

## Usage

```bash
streamlit run app.py
```

1. Use the **Sync** sidebar to pick a date range and click **Sync now**.
2. Use the **Filters** sidebar to narrow down the data by date, model, or user.
3. Charts and KPI cards update automatically based on the active filters.
