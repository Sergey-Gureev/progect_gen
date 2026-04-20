"""
Shared response checker utility.

check_response() covers three scenarios automatically:

1. No snapshot yet (first run after a test is un-skipped):
   → saves response.json, non_null_fields.json, expected_values.json
   → test passes
   → PRINTS a notice: user must review expected_values.json and remove
     any dynamic fields (timestamps, counters, auto-generated IDs)

2. Snapshot exists, all assertions pass:
   → test passes

3. Snapshot exists but assertions fail (data drifted on the API side):
   → prints what changed
   → offers an interactive prompt to update the snapshot (silent in CI)
   → re-raises AssertionError so the test stays red until confirmed
"""
import json
import sys
from pathlib import Path

from assertpy import assert_that, soft_assertions


# ── Raw serialisation ─────────────────────────────────────────────────────────

def _to_raw(obj):
    """
    Convert a Pydantic model to a plain dict using original JSON field names
    and unwrapping JsonAny.actual_instance — result matches what the API sent.

    Uses getattr() on model fields instead of model_dump() so JsonAny objects
    are still Pydantic instances when we reach them (model_dump() would already
    have serialised them to dicts, losing the actual_instance handle).
    """
    if obj is None:
        return None
    # JsonAny wrapper — return the actual value directly
    if hasattr(obj, "actual_instance"):
        return _to_raw(obj.actual_instance)
    # Regular Pydantic model — walk fields manually to keep nested models alive
    if hasattr(type(obj), "model_fields"):
        result = {}
        for field_name, field_info in type(obj).model_fields.items():
            alias = field_info.alias or field_name
            result[alias] = _to_raw(getattr(obj, field_name, None))
        return result
    if isinstance(obj, list):
        return [_to_raw(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_raw(v) for k, v in obj.items()}
    return obj


def _raw_item(response) -> dict:
    """Return the first item (or the object itself) as a raw dict."""
    item = response[0] if isinstance(response, list) else response
    raw = _to_raw(item)
    return raw if isinstance(raw, dict) else {}


# ── Snapshot helpers ──────────────────────────────────────────────────────────

_EXPECTED_VALUES_NOTICE = """
  ┌─────────────────────────────────────────────────────────────┐
  │  📋  expected_values.json created — REVIEW REQUIRED         │
  ├─────────────────────────────────────────────────────────────┤
  │  Contains ALL non-null field values from the first response. │
  │  Every value will be asserted on each subsequent test run.   │
  │                                                             │
  │  YOUR RESPONSIBILITY:                                        │
  │    ✂  Remove dynamic fields:                                │
  │       timestamps, counters, auto-generated IDs, scores      │
  │    ✔  Keep only stable fields you want to assert:           │
  │       type, status, name, fixed metadata                    │
  └─────────────────────────────────────────────────────────────┘
  Path: {path}
"""


def save_initial_snapshot(response, snapshot_dir: Path) -> None:
    """
    Save the initial expected result on first run. No-op if snapshot exists.

    Creates:
      response.json         — raw API response (first 3 items for lists)
      non_null_fields.json  — field names that were non-null (schema guard)
      expected_values.json  — all non-null field values (value guard)
                              ⚠ user must review and trim dynamic fields
    """
    if (snapshot_dir / "non_null_fields.json").exists():
        return

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    raw = _raw_item(response)

    # response.json — raw, first 3 items for lists
    sample = [_to_raw(i) for i in response[:3]] if isinstance(response, list) else _to_raw(response)
    (snapshot_dir / "response.json").write_text(json.dumps(sample, indent=2, default=str))

    # non_null_fields.json — field names only (schema-level guard)
    non_null = [k for k, v in raw.items() if v is not None]
    (snapshot_dir / "non_null_fields.json").write_text(json.dumps(non_null, indent=2))

    # expected_values.json — all non-null values (value-level guard)
    expected_values_path = snapshot_dir / "expected_values.json"
    expected_values = {k: v for k, v in raw.items() if v is not None}
    expected_values_path.write_text(json.dumps(expected_values, indent=2, default=str))
    print(_EXPECTED_VALUES_NOTICE.format(path=expected_values_path))

    print(f"  💾 Snapshot saved: {snapshot_dir.name}")


def propose_snapshot_update(response, snapshot_dir: Path) -> None:
    """
    When assertions fail (response was 200 but data drifted), show what changed
    and offer an interactive prompt to overwrite the snapshot.
    Silent in CI (stdin is not a tty).
    """
    raw = _raw_item(response)

    # Schema drift (non_null_fields)
    non_null_path = snapshot_dir / "non_null_fields.json"
    old_non_null = json.loads(non_null_path.read_text()) if non_null_path.exists() else []
    new_non_null = [k for k, v in raw.items() if v is not None]
    schema_added = sorted(set(new_non_null) - set(old_non_null))
    schema_removed = sorted(set(old_non_null) - set(new_non_null))

    # Value drift (expected_values)
    ev_path = snapshot_dir / "expected_values.json"
    old_values = json.loads(ev_path.read_text()) if ev_path.exists() else {}
    value_changed = {
        k: (old_values[k], raw.get(k))
        for k in old_values
        if k in raw and raw[k] != old_values[k]
    }

    if not schema_added and not schema_removed and not value_changed:
        return

    print(f"\n  ⚠️  Snapshot drift in {snapshot_dir.name}:")
    for f in schema_added:
        print(f"    schema  + {f}  (now non-null)")
    for f in schema_removed:
        print(f"    schema  - {f}  (now null / missing)")
    for f, (old, new) in value_changed.items():
        print(f"    value   ~ {f}: {old!r} → {new!r}")

    if not sys.stdin.isatty():
        return

    try:
        answer = input("\n  Update snapshot? [y/N] ").strip().lower()
    except EOFError:
        return

    if answer in ("y", "yes"):
        sample = [_to_raw(i) for i in response[:3]] if isinstance(response, list) else _to_raw(response)
        (snapshot_dir / "response.json").write_text(json.dumps(sample, indent=2, default=str))
        non_null_path.write_text(json.dumps(new_non_null, indent=2))
        if ev_path.exists():
            new_values = {k: v for k, v in raw.items() if v is not None}
            ev_path.write_text(json.dumps(new_values, indent=2, default=str))
        print("  ✅ Snapshot updated")


# ── Main entry point ──────────────────────────────────────────────────────────

def check_response(response, snapshot_dir: Path) -> None:
    """
    Assert that the response matches the saved snapshot.

    Flow:
      response is None      → fail immediately
      no snapshot yet       → save_initial_snapshot(), pass (first run)
      snapshot exists       → assert non_null_fields + expected_values
      assertions fail       → propose_snapshot_update(), re-raise
    """
    if response is None:
        with soft_assertions():
            assert_that(response).is_not_none()
        return

    non_null_path = snapshot_dir / "non_null_fields.json"

    if not non_null_path.exists():
        save_initial_snapshot(response, snapshot_dir)
        return

    raw = _raw_item(response)
    is_list = isinstance(response, list)

    try:
        with soft_assertions():
            if is_list:
                assert_that(len(response)).is_greater_than_or_equal_to(0)

            # Schema guard: non-null fields
            non_null_fields: list[str] = json.loads(non_null_path.read_text())
            for field in non_null_fields:
                assert_that(raw.get(field)).described_as(field).is_not_none()

            # Value guard: expected_values (only if file exists and non-empty)
            ev_path = snapshot_dir / "expected_values.json"
            if ev_path.exists():
                expected_values: dict = json.loads(ev_path.read_text())
                for field, expected in expected_values.items():
                    assert_that(raw.get(field)).described_as(field).is_equal_to(expected)

    except AssertionError:
        propose_snapshot_update(response, snapshot_dir)
        raise
