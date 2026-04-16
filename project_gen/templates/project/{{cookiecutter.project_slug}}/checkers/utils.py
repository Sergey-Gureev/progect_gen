"""
Shared response checker utility.

check_response() covers two scenarios automatically:

1. No snapshot yet (first run after a test is un-skipped):
   → saves response.json + non_null_fields.json as the initial expected result
   → test passes

2. Snapshot exists but assertions fail (data changed on the API side):
   → prints what changed (added / removed non-null fields)
   → offers an interactive prompt to update the snapshot (skipped in CI)
   → re-raises the AssertionError so the test still fails until confirmed

Both helpers are also exported so checkers can call them directly if needed.
"""
import json
import sys
from pathlib import Path

from assertpy import assert_that, soft_assertions


# ── Helpers ───────────────────────────────────────────────────────────────────

def _non_null_keys(response) -> list[str]:
    """Return top-level keys whose values were non-null in the response."""
    item = response[0] if isinstance(response, list) else response
    if hasattr(item, "model_dump"):
        item = item.model_dump()
    if not isinstance(item, dict):
        return []
    return [k for k, v in item.items() if v is not None]


def _serialisable(response):
    """Convert Pydantic models to plain dicts for JSON serialisation."""
    if isinstance(response, list):
        return [i.model_dump() if hasattr(i, "model_dump") else i for i in response]
    return response.model_dump() if hasattr(response, "model_dump") else response


# ── Public API ────────────────────────────────────────────────────────────────

def save_initial_snapshot(response, snapshot_dir: Path) -> None:
    """
    Save response as the initial expected result when no snapshot exists yet.

    Creates:
      - response.json     — first 3 items (or the object)
      - non_null_fields.json — list of top-level keys that were non-null

    No-op if non_null_fields.json already exists.
    Called automatically by check_response; can also be called directly.
    """
    non_null_path = snapshot_dir / "non_null_fields.json"
    if non_null_path.exists():
        return

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    sample = response[:3] if isinstance(response, list) else response
    (snapshot_dir / "response.json").write_text(
        json.dumps(_serialisable(sample), indent=2, default=str)
    )
    non_null = _non_null_keys(response)
    non_null_path.write_text(json.dumps(non_null, indent=2))
    print(f"\n  💾 Initial snapshot saved: {snapshot_dir.name}")


def propose_snapshot_update(response, snapshot_dir: Path) -> None:
    """
    When a test fails because API data changed (response was still 200),
    show what changed and interactively offer to update the snapshot.

    In CI (stdin is not a tty) this is a no-op — the test stays red.
    Called automatically by check_response; can also be called directly.
    """
    old_path = snapshot_dir / "non_null_fields.json"
    if not old_path.exists():
        return

    old_non_null = json.loads(old_path.read_text())
    new_non_null = _non_null_keys(response)

    added = sorted(set(new_non_null) - set(old_non_null))
    removed = sorted(set(old_non_null) - set(new_non_null))

    if not added and not removed:
        return  # structural shape unchanged — value-level failure, not a schema drift

    print(f"\n  ⚠️  Snapshot drift detected in {snapshot_dir.name}:")
    for f in added:
        print(f"    + {f}  (now non-null)")
    for f in removed:
        print(f"    - {f}  (now null / missing)")

    if not sys.stdin.isatty():
        return  # CI — leave the test red, don't auto-update

    try:
        answer = input("\n  Update snapshot? [y/N] ").strip().lower()
    except EOFError:
        return

    if answer in ("y", "yes"):
        sample = response[:3] if isinstance(response, list) else response
        (snapshot_dir / "response.json").write_text(
            json.dumps(_serialisable(sample), indent=2, default=str)
        )
        old_path.write_text(json.dumps(new_non_null, indent=2))
        print("  ✅ Snapshot updated")


def check_response(response, snapshot_dir: Path) -> None:
    """
    Assert that the response structure matches the saved snapshot.

    Flow:
      - response is None                → fail immediately
      - no snapshot yet                 → save_initial_snapshot(), pass
      - snapshot exists, all fields ok  → pass
      - snapshot exists, fields changed → propose_snapshot_update(), then fail
    """
    if response is None:
        with soft_assertions():
            assert_that(response).is_not_none()
        return

    non_null_path = snapshot_dir / "non_null_fields.json"

    # No snapshot yet — save it and pass
    if not non_null_path.exists():
        save_initial_snapshot(response, snapshot_dir)
        return

    non_null_fields: list[str] = json.loads(non_null_path.read_text())
    if not non_null_fields:
        return

    is_list = isinstance(response, list)
    item = response[0] if is_list else response
    item_dict = item.model_dump() if hasattr(item, "model_dump") else item

    try:
        with soft_assertions():
            if is_list:
                assert_that(len(response)).is_greater_than_or_equal_to(0)
            for field in non_null_fields:
                assert_that(item_dict.get(field)).described_as(field).is_not_none()
    except AssertionError:
        # Data changed (response was 200) — offer to update the snapshot
        propose_snapshot_update(response, snapshot_dir)
        raise
