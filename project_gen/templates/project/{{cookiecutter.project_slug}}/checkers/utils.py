"""
Shared response checker utilities.

check_response() — snapshot-based assertion for successful responses.
check_status_code_http() — context manager for expected HTTP error responses.

check_response() covers three scenarios:

1. No snapshot yet (first run after a test is un-skipped):
   → saves response.json, non_null_fields.json, expected_values.json
   → test passes
   → PRINTS a notice: user must review expected_values.json and remove
     any dynamic fields (timestamps, counters, auto-generated IDs)

2. Snapshot exists, all assertions pass:
   → test passes

3. Snapshot exists but assertions fail:
   → prints what changed
   → offers an interactive prompt to update the snapshot (silent in CI)
   → raises AssertionError so the test stays red until confirmed
"""
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path


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


# ── HTTP status checker ───────────────────────────────────────────────────────

@asynccontextmanager
async def check_status_code_http(expected_status: int):
    """
    Async context manager for tests that expect an HTTP error response.

    Supports two usage patterns:

        # 1. inline — inside the test body
        async with check_status_code_http(400):
            await api.create_asset(body=invalid_payload)

        # 2. decorator — wraps the entire async test function
        @check_status_code_http(404)
        async def test_get_missing_asset(api):
            await api.get_asset(id="nonexistent")

    Works with any generated client that raises exceptions with a .status
    attribute (openapi-generator asyncio clients raise ApiException /
    BadRequestException / NotFoundException etc., all carry .status).

    In CI and in interactive runs alike — no prompt, deterministic pass/fail.
    """
    try:
        yield
        raise AssertionError(
            f"\n  ✗ Expected HTTP {expected_status} but request succeeded"
        )
    except AssertionError:
        raise
    except Exception as e:
        actual = getattr(e, "status", None)
        if actual is None:
            raise  # not an HTTP exception — let it propagate as-is
        if actual != expected_status:
            reason = getattr(e, "reason", "") or ""
            body = str(getattr(e, "body", "") or "")[:300]
            detail = f" ({reason})" if reason else ""
            body_line = f"\n  Body: {body}" if body else ""
            raise AssertionError(
                f"\n  ✗ Expected HTTP {expected_status}, got HTTP {actual}{detail}{body_line}"
            ) from None
        print(f"  ✅ HTTP {expected_status} confirmed")


def _ask(prompt: str) -> bool:
    """Prompt user y/N. Returns False silently in CI."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def check_response(response, snapshot_dir: Path) -> None:
    """
    Assert that the response matches the saved snapshot.
    Test ALWAYS fails on any mismatch. Two distinct recovery paths:

    1. Value changed (field still non-null, just a different value)
       → ask: "Update expected_values.json? [y/N]"
       → yes: file updated, test passes on this run
       → no:  test stays red

    2. Field became null (was non-null in snapshot)
       → ask: "Remove from snapshot? [y/N]"  (explicit, harder decision)
       → yes: field removed, test passes on this run
       → no:  test stays red

    In CI both prompts are skipped — test always stays red on mismatch.
    """
    if response is None:
        raise AssertionError("\n  ✗ Response is None")
        return

    non_null_path = snapshot_dir / "non_null_fields.json"
    if not non_null_path.exists():
        save_initial_snapshot(response, snapshot_dir)
        return

    raw = _raw_item(response)
    is_list = isinstance(response, list)
    ev_path = snapshot_dir / "expected_values.json"
    old_values: dict = json.loads(ev_path.read_text()) if ev_path.exists() else {}
    non_null_fields: list[str] = json.loads(non_null_path.read_text())

    # ── Classify ──────────────────────────────────────────────────────────────
    null_failures = [f for f in non_null_fields if raw.get(f) is None]
    null_failures += [
        f for f in old_values
        if f not in null_failures and raw.get(f) is None and old_values[f] is not None
    ]
    value_changes = {
        f: (old_values[f], raw[f])
        for f in old_values
        if f not in null_failures and raw.get(f) is not None and raw.get(f) != old_values[f]
    }

    # ── Path 1: value changes — ask to update expected_values.json ───────────
    if value_changes:
        print(f"\n  ⚠️  {len(value_changes)} value(s) changed in {snapshot_dir.name}:")
        for f, (old, new) in value_changes.items():
            print(f"    ~ {f}: {str(old)[:80]!r} → {str(new)[:80]!r}")
        if _ask("\n  Update expected_values.json? [y/N] "):
            updated = dict(old_values)
            updated.update({f: new for f, (_, new) in value_changes.items()})
            ev_path.write_text(json.dumps(updated, indent=2, default=str))
            old_values = updated
            print("  ✅ expected_values.json updated")

    # ── Path 2: null failures — no prompt, always fail ───────────────────────
    if null_failures:
        print(f"\n  🛑 {len(null_failures)} field(s) became null in {snapshot_dir.name}:")
        for f in null_failures:
            print(f"    - {f}  (was non-null in snapshot)")
        print(
            "\n  This looks like a regression, not a data change."
            "\n  Fix the root cause, or remove the field from the snapshot manually:"
            f"\n    {non_null_path}"
            f"\n    {ev_path}"
        )

    # ── Assert against current (possibly just-updated) snapshot ───────────────
    value_failure_fields = [
        f for f in old_values if raw.get(f) != old_values[f]
    ]
    failures = (
        [f"  ✗ [{f}] became None (was non-null) — possible regression" for f in null_failures]
        + [f"  ✗ [{f}] value mismatch  ↑ see diff above" for f in value_failure_fields]
    )

    if failures:
        has_only_value_changes = not null_failures
        if has_only_value_changes and not sys.stdin.isatty():
            hint = (
                "\n\n"
                "  ────────────────────────────────────────────────────────────────\n"
                "  💡 These are value changes (fields still present, just differ).\n"
                "     To review and accept them, re-run this test with -s:\n\n"
                "       pytest -s\n\n"
                "     You will be prompted for each changed field.\n"
                "     Type  y  to update the snapshot,  n  to keep it red.\n\n"
                "  🛑 Null field failures (field → None) are NOT fixable via -s.\n"
                "     They require a code or API fix.\n"
                "  ────────────────────────────────────────────────────────────────"
            )
        else:
            hint = ""
        raise AssertionError(
            f"\n\n  {len(failures)} assertion(s) failed in {snapshot_dir.name}:\n"
            + "\n".join(failures)
            + hint
        )
