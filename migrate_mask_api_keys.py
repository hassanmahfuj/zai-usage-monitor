"""One-time migration: mask full API keys already stored in usage_records.

The billing API previously returned pre-masked keys (first-4...last-4) but now
returns full keys, so rows synced after that change no longer join against
api_key_map and show up as 'Unknown' users. This script rewrites every
unmasked usage_records.api_key to the same mask format.

Usage:
    python migrate_mask_api_keys.py --dry-run   # preview without updating
    python migrate_mask_api_keys.py             # apply

Safe to re-run — already-masked keys are left untouched, and mask_api_key()
is idempotent.
"""

import sys

from db import get_conn, mask_api_key

# Matches keys already stored in masked form: exactly 4 chars + '...' + 4 chars.
MASKED_GLOB = "????...????"


def migrate(dry_run: bool = False) -> int:
    """Mask unmasked api_key values in usage_records. Returns count updated."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, api_key FROM usage_records "
            "WHERE api_key NOT GLOB ? ORDER BY id",
            (MASKED_GLOB,),
        ).fetchall()

        if not rows:
            print("Nothing to migrate — all api_key values are already masked.")
            return 0

        if dry_run:
            print(f"[dry-run] Would update {len(rows)} row(s):")
            for row_id, key in rows[:20]:
                print(f"  id={row_id}: {key} -> {mask_api_key(key)}")
            if len(rows) > 20:
                print(f"  ... and {len(rows) - 20} more")
            return 0

        with conn:
            for row_id, key in rows:
                conn.execute(
                    "UPDATE usage_records SET api_key = ? WHERE id = ?",
                    (mask_api_key(key), row_id),
                )
        print(f"Updated {len(rows)} row(s).")
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
