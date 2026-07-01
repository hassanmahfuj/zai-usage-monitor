"""Seed the api_key_map table by fetching API keys from Z.ai.

Run manually by an admin:
    python seed_api_keys.py
    python seed_api_keys.py --dry-run   # preview without inserting

Re-running is safe — INSERT OR IGNORE skips existing api_key entries.
"""

import sys

import requests

from db import get_conn

# ---------------------------------------------------------------------------
# CONFIG — replace placeholders with real values
# ---------------------------------------------------------------------------
ORG_ID = "org-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
PROJECT_ID = "proj_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
BEARER_TOKEN = "YOUR_LOGIN_BEARER_TOKEN_HERE"
API_KEYS_URL = (
    f"https://api.z.ai/api/biz/v1/organization/{ORG_ID}"
    f"/projects/{PROJECT_ID}/api_keys?keyType=1"
)

SEED_SQL = """
INSERT OR IGNORE INTO api_key_map (username, api_key)
VALUES (?, ?)
"""


def _mask_key(api_key: str) -> str:
    """Return first-4 + '...' + last-4 mask of an API key."""
    if len(api_key) <= 8:
        return api_key
    return f"{api_key[:4]}...{api_key[-4:]}"


def fetch_api_keys(token: str) -> list[dict]:
    """Fetch the API key list from Z.ai."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(API_KEYS_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200 and body.get("success") is False:
        raise ValueError(f"API error: {body.get('msg', 'unknown')}")
    return body.get("data", [])


def seed(dry_run: bool = False) -> int:
    """Fetch API keys from Z.ai and insert new mappings. Returns count added."""
    items = fetch_api_keys(BEARER_TOKEN)
    print(f"Fetched {len(items)} key(s) from Z.ai.")

    mappings: list[tuple[str, str]] = []
    skipped = 0
    for item in items:
        if item.get("isHidden"):
            skipped += 1
            continue
        name = item.get("name")
        if not name:
            skipped += 1
            continue
        api_key = item.get("apiKey", "")
        if not api_key:
            skipped += 1
            continue
        mappings.append((name, _mask_key(api_key)))

    if dry_run:
        print(f"\n[dry-run] Would insert {len(mappings)} mapping(s) ({skipped} skipped):")
        for username, api_key in mappings:
            print(f"  {username:30s} -> {api_key}")
        return 0

    conn = get_conn()
    try:
        count = 0
        for username, api_key in mappings:
            cur = conn.execute(SEED_SQL, (username, api_key))
            count += cur.rowcount
        conn.commit()
        print(f"Inserted {count} new mapping(s) ({len(mappings) - count} already existed, {skipped} skipped).")
        return count
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    seed(dry_run=dry)
