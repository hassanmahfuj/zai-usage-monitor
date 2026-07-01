"""GitLab contributions sync engine.

Logs into a GitLab instance via the web form (one shared session), then
fetches each user's contribution calendar (/users/{username}/calendar.json)
which returns {date: count} for the trailing ~12 months. Upserts the per-day
counts into the local SQLite `contributions` table.

Mirrors the structure of zai_sync.py: paginated/retry fetch + idempotent
upsert + a summary dict for the UI.
"""

import sys
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from db import get_conn, get_mapped_usernames

MAX_RETRIES = 2
RETRY_BACKOFF = 1.5  # seconds

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "Chrome/127.0 Safari/537.36"
)

UPSERT_SQL = """
INSERT INTO contributions (username, contribution_date, count)
VALUES (?, ?, ?)
ON CONFLICT(username, contribution_date) DO UPDATE SET
    count = excluded.count,
    synced_at = CURRENT_TIMESTAMP
"""


def gitlab_login(base_url: str, username: str, password: str) -> requests.Session:
    """Log into GitLab via the web form. Returns an authenticated session.

    Raises RuntimeError if login fails.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    login_page_url = f"{base_url}/users/sign_in"
    r = session.get(login_page_url)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    if not form:
        raise RuntimeError("GitLab login page: no <form> found")

    action = form.get("action") or ""
    login_url = action if action.startswith("http") else f"{base_url}{action}"

    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        # Support both modern GitLab ("user[login]") and bare ("username")
        # field naming — the live form's convention depends on the version.
        if name in ("user[login]", "username"):
            value = username
        elif name in ("user[password]", "password"):
            value = password
        elif name in ("user[remember_me]", "remember_me"):
            value = "1"
        else:
            value = inp.get("value", "")
        payload[name] = value

    resp = session.post(
        login_url,
        data=payload,
        headers={"Referer": login_page_url},
        allow_redirects=True,
    )

    login_failed = (
        resp.url.rstrip("/") == login_page_url.rstrip("/")
        or "Invalid Login or password" in resp.text
        or ("Sign in" in resp.text and "Sign out" not in resp.text)
    )
    if login_failed:
        raise RuntimeError(
            f"GitLab login failed for user '{username}' (redirected back to sign_in)"
        )

    return session


def fetch_calendar(
    session: requests.Session,
    base_url: str,
    username: str,
) -> dict:
    """Fetch one user's contribution calendar. Returns {date_str: count}.

    Retries transient errors. Raises RuntimeError after exhausting retries.
    """
    url = f"{base_url}/users/{username}/calendar.json"
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                return {}  # user has no calendar / does not exist
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError(f"unexpected calendar payload: {type(data).__name__}")
            return data
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))

    raise RuntimeError(
        f"Failed to fetch calendar for '{username}' after {MAX_RETRIES + 1} attempts: {last_error}"
    )


def upsert_calendar(username: str, calendar: dict, start_date: date, end_date: date) -> int:
    """Upsert a user's filtered calendar entries. Returns count of rows touched.

    Only entries with date within [start_date, end_date] are stored. Entries
    whose value is 0 are skipped (no contribution that day) to keep the table
    small and the leaderboard's contribution total meaningful.
    """
    rows: list[tuple[str, str, int]] = []
    for date_str, count in calendar.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue  # skip malformed keys
        if not (start_date <= d <= end_date):
            continue
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            continue
        if count_int <= 0:
            continue
        rows.append((username, date_str, count_int))

    if not rows:
        return 0

    conn = get_conn()
    try:
        conn.executemany(UPSERT_SQL, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def sync_contributions(
    start_date: date,
    end_date: date,
    base_url: str,
    username: str,
    password: str,
) -> dict:
    """Run a full contribution sync for the given date range.

    Returns a summary dict:
        users_processed, dates_upserted, skipped_no_calendar, failed_users
    """
    summary = {
        "users_processed": 0,
        "dates_upserted": 0,
        "skipped_no_calendar": 0,
        "failed_users": 0,
    }

    try:
        session = gitlab_login(base_url, username, password)
    except RuntimeError as e:
        print(f"[contrib_sync] aborting: {e}", file=sys.stderr)
        summary["failed_users"] = -1  # signal login failure to the UI
        return summary

    for uname in get_mapped_usernames():
        try:
            calendar = fetch_calendar(session, base_url, uname)
        except RuntimeError as e:
            print(f"[contrib_sync] {uname}: {e}", file=sys.stderr)
            summary["failed_users"] += 1
            continue

        if not calendar:
            summary["skipped_no_calendar"] += 1
            continue

        summary["dates_upserted"] += upsert_calendar(uname, calendar, start_date, end_date)
        summary["users_processed"] += 1

    return summary
