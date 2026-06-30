# Z.ai Billing Dashboard — Spec Plan

Streamlit + SQLite dashboard for visualizing Z.ai token usage, cost, and request volume, with a manual sync button against the Z.ai billing endpoint.

## 1. Tech stack

- **Frontend/app**: Streamlit
- **Storage**: SQLite (single file, e.g. `data/billing.db`), accessed via Python `sqlite3` or SQLAlchemy
- **HTTP**: `requests`
- **Data wrangling**: `pandas`
- **Charts**: Plotly (`plotly.express`) — good native support for stacked/grouped bar charts with a color dimension, plus interactive filtering/hover
- **Secrets**: `.streamlit/secrets.toml` for `customerId` and bearer token (never hardcoded, never in the mapping table)

## 2. Database schema

### 2.1 `api_key_map`
Populated only by an admin script (`seed_api_keys.py`), never via the Streamlit UI.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `username` | TEXT NOT NULL | human-readable owner of the key |
| `api_key` | TEXT NOT NULL UNIQUE | the **masked** key exactly as Z.ai returns it, e.g. `80b3...a568` — Z.ai never exposes the full key, so this is the only form we can match on |
| `note` | TEXT | optional, e.g. which package/plan |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

```sql
CREATE TABLE IF NOT EXISTS api_key_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 `usage_records`

(table name confirmed: `usage_records`, not `billing_records`)
Populated only by the sync job, from the Z.ai `bill/day` endpoint.

| Column | Source field | Type | Notes |
|---|---|---|---|
| `id` | — | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `billing_no` | `billingNo` | TEXT NOT NULL UNIQUE | dedup key — one row per billing line, idempotent re-sync |
| `billing_date` | `billingDate` | DATE NOT NULL | stored as `YYYY-MM-DD` text, sortable as-is in SQLite |
| `api_key` | `apiKey` | TEXT NOT NULL | masked key, joins to `api_key_map.api_key` |
| `model_code` | `modelCode` | TEXT NOT NULL | e.g. `glm-4.7` |
| `cost_price` | `costPrice` | REAL | price per `costUnit` (kToken) |
| `token_usage` | `usageCount` | INTEGER | raw token count for this line |
| `token_type` | `tokenType` | TEXT | `INPUT` / `OUTPUT` / `CACHE` (confirm exact values once real data is synced — see Open Questions) |
| `requests` | `apiUsage` | INTEGER | request count for this line |
| `synced_at` | — | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | when the row was inserted locally |

```sql
CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    billing_no TEXT NOT NULL UNIQUE,
    billing_date DATE NOT NULL,
    api_key TEXT NOT NULL,
    model_code TEXT NOT NULL,
    cost_price REAL,
    token_usage INTEGER,
    token_type TEXT,
    requests INTEGER,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_billing_date ON usage_records(billing_date);
CREATE INDEX IF NOT EXISTS idx_billing_model ON usage_records(model_code);
CREATE INDEX IF NOT EXISTS idx_billing_apikey ON usage_records(api_key);
```

No hard foreign key constraint from `usage_records.api_key` to `api_key_map.api_key` — a key may appear in billing data before it's been mapped to a username. The UI should display unmapped keys as `Unknown (<masked key>)` rather than dropping them, so nothing silently disappears from the charts.

## 3. `api_key_map` seed script

`seed_api_keys.py`, run manually by an admin, not exposed in the Streamlit UI per the requirement.

- Input: a small CSV or a hardcoded `dict` in the script, e.g. `{"alice": "80b3...a568", "bob": "9f2c...11de"}`
- Behavior: `INSERT OR REPLACE INTO api_key_map (username, api_key) VALUES (?, ?)` for each entry, so re-running the script is safe and updates existing mappings.
- Run with `python seed_api_keys.py` whenever a new key/teammate needs to be added.

## 4. Z.ai sync mechanism

### 4.1 Endpoint contract

```
GET https://api.z.ai/api/platform-charge-zai/bill/day
  ?customerId={customerId}
  &billingPeriod={YYYY-MM}
  &pageNum={n}
  &pageSize=100
Authorization: Bearer {token}
```

- `billingPeriod` only accepts a **year-month**, not a day — so a user-selected date range must first be expanded into the set of distinct months it spans.
- `pageSize` max is 100.
- Response gives `data.pages` (total pages for that month) and `data.records` (the page's rows).

### 4.2 Algorithm

```
function sync(start_date, end_date, customer_id, token):
    months = distinct_year_months(start_date, end_date)   # e.g. ["2026-05", "2026-06"]
    summary = {fetched: 0, inserted: 0, skipped_duplicate: 0, skipped_out_of_range: 0}

    for period in months:
        page = 1
        while True:
            resp = GET bill/day?customerId&billingPeriod=period&pageNum=page&pageSize=100
            records = resp.data.records
            total_pages = resp.data.pages

            in_range = []
            for r in records:
                d = parse(r.billingDate)
                if start_date <= d <= end_date:
                    in_range.append(r)
                else:
                    summary.skipped_out_of_range += 1

            upsert_all(in_range)   # see 4.3
            summary.fetched += len(records)

            # early exit: records are sorted so once every record on this
            # page is older than start_date, no later page in this month
            # can contain in-range rows (confirmed sort order)
            if all(parse(r.billingDate) < start_date for r in records):
                break
            if page >= total_pages:
                break
            page += 1

    return summary
```

### 4.3 Upsert / dedup

```sql
INSERT OR IGNORE INTO usage_records
  (billing_no, billing_date, api_key, model_code, cost_price, token_usage, token_type, requests)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

`billing_no` is unique per Z.ai billing line, so `INSERT OR IGNORE` makes re-syncing the same range a no-op for rows already stored — safe to click "Sync" repeatedly, including overlapping ranges. (If Z.ai ever mutates a row's status after the fact — e.g. `billingStatus` flips from `Unpaid` to `Paid` — note that since we don't store `billingStatus`, this isn't currently tracked; switch to `INSERT OR REPLACE` if that needs to be reflected, see Open Questions.)

### 4.4 Error handling / resilience

- Wrap each page fetch in try/except; on HTTP error or malformed JSON, record the failure, skip that page, and continue with the next month rather than aborting the whole sync.
- Respect `code != 200` or `success: false` in the response body as a failure, not just HTTP status.
- Add a basic retry (e.g. 2 retries with short backoff) per page request for transient network errors.
- Surface a final summary in the Streamlit UI: months processed, total fetched, inserted, duplicates skipped, any failed pages/months.

### 4.5 Auth & config

- `customerId` and bearer token live in `.streamlit/secrets.toml`:
  ```toml
  [zai]
  customer_id = "18451770700221195"
  bearer_token = "..."
  ```
- Never hardcode the token in source or commit it. Optionally allow an admin-only override field in the sidebar for testing a different token, defaulting to the secret.

## 5. Streamlit app structure

### 5.1 File layout

```
zai_dashboard/
├── app.py                  # Streamlit entrypoint
├── db.py                   # schema init, connection, query helpers
├── zai_sync.py             # fetch_billing_period(), sync(), upsert logic
├── seed_api_keys.py        # admin-run mapping seeder (not UI-exposed)
├── data/
│   └── billing.db
├── .streamlit/
│   └── secrets.toml
└── requirements.txt
```

### 5.2 Sidebar

- **Sync section**
  - Date range picker, default = today (single-day range, `start = end = date.today()`)
  - "Sync now" button → calls `zai_sync.sync()`, shows a spinner, then the result summary (fetched / inserted / duplicates / failures)
- **Filter section** (drives all three charts)
  - Date range picker (separate from the sync range; default = last 30 days, or full available data range)
  - Model multiselect — options populated from `SELECT DISTINCT model_code FROM usage_records`
  - Username multiselect — options populated from `SELECT DISTINCT username FROM api_key_map` plus an `"Unknown"` bucket for unmapped keys
- **Group by** selector for chart x-axis: `Date` / `Model` / `Username` (default `Date`)

### 5.3 Main area

- Three KPI cards at the top: total tokens, total cost, total requests for the current filter.
- **Token usage chart**: stacked bar, x-axis = selected group-by dimension, bar segments colored by `token_type` (INPUT / OUTPUT / CACHE), y = sum of `token_usage`.
- **Cost chart**: bar, x-axis = group-by dimension, y = computed cost (see §6), optionally stacked by `token_type` or `model_code` depending on group-by choice.
- **Requests chart**: bar, x-axis = group-by dimension, y = sum of `requests`.
- Optional: an expandable "raw data" table below the charts showing the filtered rows (with `username` resolved via the join), for spot-checking.

### 5.4 Filter → query logic

All three charts and the KPI cards share one filtered base query:

```sql
SELECT b.*, COALESCE(m.username, 'Unknown') AS username
FROM usage_records b
LEFT JOIN api_key_map m ON b.api_key = m.api_key
WHERE b.billing_date BETWEEN :start AND :end
  AND (:models IS NULL OR b.model_code IN (:models))
  AND (:usernames IS NULL OR COALESCE(m.username, 'Unknown') IN (:usernames))
```

Pull this into a pandas DataFrame once per render, then derive the three charts from it in-memory (group-by + aggregate) rather than issuing three separate SQL queries — simpler and fast enough at this data volume.

## 6. Cost calculation

`cost_price` is the **per-kToken rate** for that line item (price per 1,000 tokens). The actual cost is computed as:

```
cost = cost_price * token_usage / 1000
```

The cost chart and KPI card sum this computed value, grouped by whatever the x-axis dimension is.

## 7. Assumptions — confirmed

1. **Sort order** — confirmed records within a page are sorted so the early-exit in §4.2 is safe (once a page is entirely older than `start_date`, no later page in that month has in-range rows). Keeping the early-exit as designed.
2. **`token_type` values** — confirmed: `INPUT`, `OUTPUT`, `CACHE`. These three are used as the stacked-bar color legend for the token usage chart.
3. **Cost field** — `cost_price` is the **per-kToken rate**, not the total cost. Actual cost is computed as `cost_price * token_usage / 1000`; see §6.
4. **Re-sync of already-paid rows** — confirmed `INSERT OR IGNORE` on `billing_no` is fine; `billingStatus` doesn't need to be tracked.

One still-open item:

5. **Default sync range** — "default current date" is implemented as a single-day range (today only, `start = end = date.today()`). Flag if month-to-date or something else was intended instead.

## 8. requirements.txt

```
streamlit
requests
pandas
plotly
```
