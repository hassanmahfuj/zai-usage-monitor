"""Z.ai billing API sync engine.

Fetches daily billing records via paginated API calls and upserts them
into the local SQLite database.
"""

import time
from datetime import date, datetime

import requests

from db import get_conn

API_BASE = "https://api.z.ai/api/platform-charge-zai/bill/day"
PAGE_SIZE = 100
MAX_RETRIES = 2
RETRY_BACKOFF = 1.5  # seconds


def get_distinct_months(start_date: date, end_date: date) -> list[str]:
    """Expand a date range into sorted distinct 'YYYY-MM' period strings."""
    months: list[str] = []
    current = date(start_date.year, start_date.month, 1)
    end_month = date(end_date.year, end_date.month, 1)
    while current <= end_month:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def fetch_billing_period(
    customer_id: str,
    token: str,
    billing_period: str,
    page: int,
) -> dict:
    """Fetch a single page of billing records for a given month.

    Returns the full response dict on success. Raises on HTTP/JSON errors
    after exhausting retries.
    """
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "customerId": customer_id,
        "billingPeriod": billing_period,
        "pageNum": page,
        "pageSize": PAGE_SIZE,
    }

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(API_BASE, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 200 and body.get("success") is False:
                raise ValueError(f"API error: {body.get('msg', 'unknown')}")
            return body
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))

    raise RuntimeError(
        f"Failed to fetch {billing_period} page {page} after {MAX_RETRIES + 1} attempts: {last_error}"
    )


def upsert_records(records: list[dict]) -> int:
    """Insert billing records, ignoring duplicates. Returns count of new rows."""
    sql = """
    INSERT OR IGNORE INTO usage_records
        (billing_no, billing_date, api_key, model_code, cost_price,
         token_usage, token_type, requests)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    conn = get_conn()
    try:
        count = 0
        for r in records:
            if "apiKey" not in r:
                continue
            cur = conn.execute(sql, (
                str(r["billingNo"]),
                r["billingDate"],
                r["apiKey"],
                r["modelCode"],
                r.get("costPrice"),
                r.get("usageCount"),
                r.get("tokenType"),
                r.get("apiUsage"),
            ))
            count += cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def sync(
    start_date: date,
    end_date: date,
    customer_id: str,
    token: str,
) -> dict:
    """Run a full sync for the given date range.

    Returns a summary dict with keys:
        months_processed, fetched, inserted, skipped_out_of_range, failed_pages
    """
    months = get_distinct_months(start_date, end_date)
    summary = {
        "months_processed": 0,
        "fetched": 0,
        "inserted": 0,
        "skipped_out_of_range": 0,
        "failed_pages": 0,
    }

    for period in months:
        page = 1
        period_failed = False
        while True:
            try:
                body = fetch_billing_period(customer_id, token, period, page)
            except RuntimeError:
                summary["failed_pages"] += 1
                period_failed = True
                break

            data = body.get("data", {})
            records = data.get("records", [])
            total_pages = data.get("pages", 1)

            in_range: list[dict] = []
            for r in records:
                d = datetime.strptime(r["billingDate"], "%Y-%m-%d").date()
                if start_date <= d <= end_date:
                    in_range.append(r)
                else:
                    summary["skipped_out_of_range"] += 1

            summary["inserted"] += upsert_records(in_range)
            summary["fetched"] += len(records)

            # Early exit: once every record on this page is older than
            # start_date, no later page can contain in-range rows.
            if all(
                datetime.strptime(r["billingDate"], "%Y-%m-%d").date() < start_date
                for r in records
            ):
                break
            if page >= total_pages:
                break
            page += 1

        summary["months_processed"] += 1 if not period_failed else 0

    return summary
