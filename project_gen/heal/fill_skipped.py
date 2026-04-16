"""
fill_skipped.py — auto-fill skipped tests using real API responses.

Flow:
  1. Find all @pytest.mark.skip tests (no reason = auto-generated)
  2. Resolve parameter values from spec (enums, required flags)
  3. Make a real HTTP request to get an actual response
  4. Save response to expected_result/{service}/{api}/{test}/response.json
  5. Generate assertions algorithmically from response_type + real data
  6. Show diff to user → confirm → write test file
"""
import asyncio
import difflib
import json
import os
import sys
from pathlib import Path

import aiohttp
import toml

try:
    from .test_parser import parse_skipped_tests, SkippedTest, TestParam
    from .spec_reader import load_spec, find_endpoint
except ImportError:
    from project_gen.heal.test_parser import parse_skipped_tests, SkippedTest, TestParam  # type: ignore[no-redef]
    from project_gen.heal.spec_reader import load_spec, find_endpoint  # type: ignore[no-redef]


# ── Entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path, service_filter: str | None = None, test_filter: str | None = None, auto: bool = False) -> None:
    config = toml.load(project_dir / "testproject.toml")
    services = config.get("http", [])
    if not services:
        print("No services in testproject.toml")
        return

    skipped = parse_skipped_tests(project_dir / "tests")
    if not skipped:
        print("✅ No auto-generated skipped tests found.")
        return

    print(f"🔍 Found {len(skipped)} skipped tests\n")

    for svc in services:
        name = svc["service_name"]
        if service_filter and name != service_filter:
            continue

        svc_tests = [t for t in skipped if t.fixture_name.startswith(name)]
        if test_filter:
            svc_tests = [t for t in svc_tests if test_filter in t.function_name]
        if not svc_tests:
            continue

        print(f"📦 Service: {name} ({len(svc_tests)} tests)")
        print(f"   Loading spec from {svc['swagger_url']}...")
        try:
            spec = load_spec(svc["swagger_url"])
        except Exception as e:
            print(f"   ⚠️  Could not load spec: {e}")
            continue

        base_url = svc["base_url"]
        headers = _load_auth_headers(project_dir, name)

        for test in svc_tests:
            _process_test(test, spec, base_url, headers, project_dir, auto)


# ── Process single test ───────────────────────────────────────────────────────

def _process_test(
    test: SkippedTest,
    spec: dict,
    base_url: str,
    headers: dict,
    project_dir: Path,
    auto: bool,
) -> None:
    rel = test.file_path.relative_to(project_dir)
    print(f"\n  🔧 {rel}")

    endpoint = find_endpoint(spec, test.api_method)
    if not endpoint:
        print(f"     ⚠️  Endpoint not found in spec for '{test.api_method}', skipping")
        return

    print(f"     {endpoint['method']} {endpoint['path']} — {endpoint['summary']}")

    method = endpoint["method"].upper()

    # DELETE — skip always (destructive, needs real data)
    if method == "DELETE":
        print(f"     ⏭  DELETE — destructive, fill manually")
        return

    # Resolve path/query parameter values from spec enums
    param_values, missing = _resolve_params(test.params, endpoint)
    if missing:
        print(f"     ⏭  Needs real values for: {missing} — fill manually")
        return

    # Make real request (up to MAX_ATTEMPTS)
    print(f"     📡 Requesting with params: {param_values}")
    try:
        response_data, skip_reason, attempts_log, successful_body = asyncio.run(
            _real_request(base_url, endpoint, param_values, headers)
        )
    except Exception as e:
        print(f"     ⚠️  Request failed: {e}")
        return

    if response_data is None:
        print(f"     ⏭  {skip_reason}")
        # Keep @pytest.mark.skip but add TODO so user knows what to fix
        todo_source = _inject_todo(test, endpoint, skip_reason, attempts_log)
        if todo_source != test.source:
            test.file_path.write_text(todo_source)
            print(f"     📝 TODO added (skip kept)")
        return

    print(f"     ✅ Got response ({_describe_response(response_data)})")

    # Save to expected_result/
    snapshot_path = _save_snapshot(
        project_dir, test, endpoint, param_values, response_data
    )
    print(f"     💾 Saved to {snapshot_path.relative_to(project_dir)}")

    # Derive service name from fixture (e.g. "cyco_app_assets_api" → "cyco_app")
    service_name = _service_name_from_fixture(test.fixture_name)

    # Save non_null_fields.json alongside response.json
    non_null = _get_non_null_fields(
        project_dir, service_name,
        endpoint.get("response_model_class"),
        response_data, endpoint["response_type"],
    )
    if non_null is not None:
        (snapshot_path / "non_null_fields.json").write_text(
            json.dumps(non_null, indent=2)
        )

    # Generate checker module and lean test
    checker_file = _checker_path(project_dir, test)
    checker_source = _generate_checker(test, project_dir)
    new_test_source = _generate_test(test, endpoint, param_values, successful_body)

    # Show diff and ask for confirmation
    if _confirm_changes(test, new_test_source, auto):
        # Copy utils.py template to checkers/ if not present
        _ensure_checker_utils(project_dir)
        # Write checker (create dirs + __init__.py as needed)
        checker_file.parent.mkdir(parents=True, exist_ok=True)
        _ensure_init_files(checker_file.parent, project_dir / "checkers")
        checker_file.write_text(checker_source)
        print(f"     📋 Checker: {checker_file.relative_to(project_dir)}")

        test.file_path.write_text(new_test_source)
        print(f"     ✅ Written")
    else:
        print(f"     ⏭  Skipped")


# ── Parameter resolution ──────────────────────────────────────────────────────

def _resolve_params(
    test_params: list[TestParam],
    endpoint: dict,
) -> tuple[dict, list[str]]:
    """
    Build a dict of param_name → value for required parameters.
    Returns (values, missing) where missing = required params we can't fill.
    """
    spec_params = {p["name"]: p for p in endpoint.get("parameters", [])}
    values = {}
    missing = []

    for tp in test_params:
        if not tp.required:
            continue  # optional → omit (use API default)

        # Normalise name: test uses snake_case, spec may use kebab-case
        spec_key = tp.name.replace("_", "-")
        spec_param = spec_params.get(tp.name) or spec_params.get(spec_key)

        if spec_param and spec_param.get("enum"):
            values[tp.name] = spec_param["enum"][0]
        else:
            missing.append(tp.name)

    return values, missing


# ── Real HTTP request with retry loop ────────────────────────────────────────

MAX_ATTEMPTS = 3


async def _real_request(
    base_url: str,
    endpoint: dict,
    param_values: dict,
    headers: dict,
) -> tuple[dict | list | None, str, list[dict], dict | list | None]:
    """
    Make a real HTTP request with up to MAX_ATTEMPTS retry strategies.

    Returns (response_data, skip_reason, attempts_log, successful_body).
    - response_data is None when all attempts failed
    - successful_body is the request body that produced the 200 (None for GET/DELETE)
    - attempts_log is used to build TODO comment in the test
    """
    method = endpoint["method"].upper()
    path = endpoint["path"]

    for name, value in param_values.items():
        path = path.replace(f"{{{name}}}", str(value))
        path = path.replace(f"{{{name.replace('_', '-')}}}", str(value))

    url = base_url.rstrip("/") + path

    query_params = {
        k: v for k, v in param_values.items()
        if f"{{{k}}}" not in endpoint["path"]
        and f"{{{k.replace('_', '-')}}}" not in endpoint["path"]
    }

    timeout = aiohttp.ClientTimeout(total=15)
    all_headers = {"Content-Type": "application/json", **headers}
    attempts_log: list[dict] = []

    async with aiohttp.ClientSession(headers=all_headers, timeout=timeout) as session:
        req_kwargs: dict = {"ssl": False}
        if query_params:
            req_kwargs["params"] = query_params

        # Build initial body from spec schema (attempt 1)
        body = _body_from_schema(endpoint.get("request_body")) if method in ("POST", "PUT", "PATCH") else None
        if body is not None:
            req_kwargs["json"] = body

        for attempt in range(1, MAX_ATTEMPTS + 1):
            async with session.request(method, url, **req_kwargs) as resp:
                status = resp.status
                try:
                    resp_data = await resp.json(content_type=None)
                except Exception:
                    resp_data = {"_raw_text": await resp.text()}

                attempts_log.append({"attempt": attempt, "body": req_kwargs.get("json"), "status": status, "response": resp_data})

                # Success
                if status in (200, 201):
                    return resp_data, "", attempts_log, req_kwargs.get("json")

                # Stop immediately — no retry will help
                if status == 403:
                    return None, "HTTP 403 — check auth token or permissions", attempts_log, None
                if status == 404:
                    return None, "HTTP 404 — needs a real resource ID, fill manually", attempts_log, None

                # For non-400 or GET — no retry strategy
                if status != 400 or method not in ("POST", "PUT", "PATCH"):
                    return None, f"HTTP {status}", attempts_log, None

                if attempt == MAX_ATTEMPTS:
                    break

                # Build next attempt body from error response
                next_body = _next_body_from_error(resp_data, req_kwargs.get("json"), endpoint.get("request_body"))
                if next_body is None:
                    break  # no improvement possible
                if next_body == req_kwargs.get("json"):
                    break  # body didn't change — would loop

                print(f"     🔄 Attempt {attempt + 1} with body: {str(next_body)[:80]}")
                req_kwargs["json"] = next_body

        # All attempts failed — build summary for TODO
        todo_hint = _build_todo_hint(attempts_log)
        return None, todo_hint, attempts_log, None


def _body_from_schema(schema: dict | None) -> dict | list:
    """
    Build an initial request body from the spec schema required fields.
    This is attempt 1 — best-effort from schema alone.
    """
    if not schema:
        return {}

    schema_type = schema.get("type")

    # Array body (e.g. search endpoints that take a list of filters)
    if schema_type == "array":
        return []

    # Object body — fill required fields
    body = {}
    required_fields = schema.get("required", [])
    properties = schema.get("properties", {})

    for field in required_fields:
        prop = properties.get(field, {})
        body[field] = _minimal_value(field, prop)

    # Also fill non-required fields that have enums (safe to include)
    for field, prop in properties.items():
        if field not in body and prop.get("enum"):
            body[field] = prop["enum"][0]

    return body


def _next_body_from_error(
    error_body: dict,
    current_body: dict | list | None,
    schema: dict | None,
) -> dict | list | None:
    """
    Given a 400 error response, produce an improved body for the next attempt.
    Returns None if no improvement is possible.
    """
    explanation = error_body.get("explanation") if isinstance(error_body, dict) else None
    if not explanation:
        return None

    # "invalid type" on a list means body should be [] not {}
    if isinstance(explanation, list):
        if any("invalid type" in str(e) for e in explanation):
            return [] if not isinstance(current_body, list) else None
        return None

    # Dict explanation: {"field": ["missing required key"]}
    if isinstance(explanation, dict):
        missing_fields = [
            f for f, errors in explanation.items()
            if any("missing required" in str(e) for e in errors)
        ]
        if not missing_fields:
            return None

        # Start from current body and add missing fields
        base = dict(current_body) if isinstance(current_body, dict) else {}
        properties = schema.get("properties", {}) if schema else {}
        for field in missing_fields:
            if field not in base:
                base[field] = _minimal_value(field, properties.get(field, {}))
        return base

    return None


def _minimal_value(field_name: str, prop: dict) -> object:
    """Produce a minimal placeholder value for a field based on its schema."""
    enum = prop.get("enum")
    if enum:
        return enum[0]

    field_type = prop.get("type", "")

    if field_type == "array" or field_name.endswith("_ids") or field_name.endswith("ids"):
        return []
    if field_type == "integer":
        return 0
    if field_type == "boolean":
        return False
    if field_type == "number":
        return 0.0
    # string / object / unknown
    return ""


def _build_todo_hint(attempts_log: list[dict]) -> str:
    """Build a human-readable hint from all failed attempts."""
    last = attempts_log[-1]
    explanation = last["response"].get("explanation") if isinstance(last["response"], dict) else None

    if isinstance(explanation, dict):
        missing = [f for f, errs in explanation.items() if any("missing" in str(e) for e in errs)]
        if missing:
            return f"HTTP {last['status']} after {len(attempts_log)} attempts — still missing: {missing}"

    return f"HTTP {last['status']} after {len(attempts_log)} attempts — fill body manually"


# ── Snapshot saving ───────────────────────────────────────────────────────────

def _save_snapshot(
    project_dir: Path,
    test: SkippedTest,
    endpoint: dict,
    param_values: dict,
    response_data: dict | list,
) -> Path:
    # expected_result/{service}/{api_group}/{test_name}/
    parts = test.file_path.relative_to(project_dir / "tests").parts
    snapshot_dir = project_dir / "expected_result" / Path(*parts[:-1])
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Save first 3 items if list (avoid huge files)
    sample = response_data[:3] if isinstance(response_data, list) else response_data
    (snapshot_dir / "response.json").write_text(
        json.dumps(sample, indent=2, default=str)
    )

    # Save endpoint metadata for heal comparison
    (snapshot_dir / "endpoint.json").write_text(
        json.dumps({
            "method": endpoint["method"],
            "path": endpoint["path"],
            "params_used": param_values,
            "response_type": endpoint["response_type"],
        }, indent=2)
    )

    return snapshot_dir


# ── Test generation ───────────────────────────────────────────────────────────

def _inject_todo(
    test: SkippedTest,
    endpoint: dict,
    skip_reason: str,
    attempts_log: list[dict],
) -> str:
    """
    Keep @pytest.mark.skip and insert a TODO comment inside the test body
    so the user knows exactly what to fix.
    """
    lines = test.source.splitlines()

    # Build TODO block (indented for inside function body)
    todo_lines = [
        f"    # TODO: fill_skipped could not complete this test automatically",
        f"    # Endpoint: {endpoint['method']} {endpoint['path']}",
        f"    # Reason: {skip_reason}",
    ]
    if attempts_log:
        for a in attempts_log:
            todo_lines.append(f"    #   attempt {a['attempt']}: body={str(a.get('body'))[:60]} → HTTP {a['status']}")

    # Insert TODO after the function signature line, before existing body
    final_lines = []
    inserted = False
    for line in lines:
        final_lines.append(line)
        if not inserted and line.strip().startswith("async def test_") and line.strip().endswith(":"):
            final_lines.extend(todo_lines)
            inserted = True

    return "\n".join(final_lines) + "\n"


def _ensure_init_files(directory: Path, stop_at: Path) -> None:
    """Create __init__.py in directory and all parents up to (but not including) stop_at."""
    current = directory
    while current != stop_at and current != current.parent:
        init = current / "__init__.py"
        if not init.exists():
            init.write_text("")
        current = current.parent
    # Also create __init__.py in stop_at itself
    root_init = stop_at / "__init__.py"
    if not root_init.exists():
        root_init.write_text("")


def _checker_path(project_dir: Path, test: SkippedTest) -> Path:
    """
    tests/cyco_app/assets_api/retrieve_unified_assets/test_retrieve_unified_assets.py
    → checkers/cyco_app/assets_api/retrieve_unified_assets.py
    """
    parts = test.file_path.relative_to(project_dir / "tests").parts
    # parts = ("cyco_app", "assets_api", "retrieve_unified_assets", "test_retrieve_unified_assets.py")
    # Drop the last file, use the folder name as checker module name
    checker_dir = project_dir / "checkers" / Path(*parts[:-2])
    checker_file = checker_dir / f"{parts[-2]}.py"
    return checker_file


def _checker_import(project_dir: Path, test: SkippedTest) -> str:
    """Build the Python import statement for the checker function."""
    parts = test.file_path.relative_to(project_dir / "tests").parts
    # e.g. checkers.cyco_app.assets_api.retrieve_unified_assets
    module = "checkers." + ".".join(parts[:-2]) + f".{parts[-2]}"
    checker_fn = _checker_fn_name(test)
    return f"from {module} import {checker_fn}"


def _checker_fn_name(test: SkippedTest) -> str:
    """test_retrieve_unified_assets → check_retrieve_unified_assets"""
    name = test.function_name
    if name.startswith("test_"):
        name = "check_" + name[5:]
    return name


_CHECKER_TEMPLATE = """\
from pathlib import Path
from checkers.utils import check_response


def {fn_name}(response) -> None:
    check_response(
        response,
        snapshot_dir=Path(__file__).parents[{depth}] / "expected_result" / {snapshot_parts},
    )
"""


def _generate_checker(test: SkippedTest, project_dir: Path) -> str:
    """
    Generate a tiny checker that delegates to check_response().
    The checker reads non_null_fields.json from expected_result/ at runtime —
    no hardcoded field names, works for any model from any service.
    """
    fn_name = _checker_fn_name(test)

    # Depth: how many parents from checker file to project root
    checker_file = _checker_path(project_dir, test)
    depth = len(checker_file.relative_to(project_dir).parts) - 1  # -1 for the file itself

    # Snapshot path parts relative to project root (as "a" / "b" / "c")
    parts = test.file_path.relative_to(project_dir / "tests").parts
    # parts = (service, api_group, test_folder, test_file.py)
    snapshot_rel = parts[:-1]  # drop the .py file
    snapshot_parts = " / ".join(f'"{p}"' for p in snapshot_rel)

    return _CHECKER_TEMPLATE.format(
        fn_name=fn_name,
        depth=depth,
        snapshot_parts=snapshot_parts,
    )


def _generate_test(
    test: SkippedTest,
    endpoint: dict,
    param_values: dict,
    successful_body: dict | list | None = None,
) -> str:
    # Identify which param is the request body (not a path/query spec param)
    spec_param_names = {p["name"] for p in endpoint.get("parameters", [])}
    spec_param_names_kebab = {p["name"].replace("-", "_") for p in endpoint.get("parameters", [])}

    # Build the API call with filled params
    call_args = []
    for tp in test.params:
        if tp.name in param_values:
            val = param_values[tp.name]
            call_args.append(f'        {tp.name}="{val}"' if isinstance(val, str) else f'        {tp.name}={val}')
        elif tp.name not in spec_param_names and tp.name not in spec_param_names_kebab and successful_body is not None:
            # This param corresponds to the request body — inject the successful value
            call_args.append(f"        {tp.name}={repr(successful_body)}")
        elif not tp.required:
            call_args.append(f"        {tp.name}=None")

    call_str = (
        f"    response = await {test.fixture_name}.{test.api_method}(\n"
        + ",\n".join(call_args)
        + "\n    )"
    )

    checker_fn = _checker_fn_name(test)

    # Rebuild imports from original source (keep existing), add checker import
    lines = test.source.splitlines()
    import_block = []
    in_imports = True
    for line in lines:
        if in_imports and (line.startswith("import ") or line.startswith("from ") or line == ""):
            import_block.append(line)
        else:
            in_imports = False

    # Remove assertpy from test imports (it lives in the checker now)
    import_block = [l for l in import_block if "assertpy" not in l]
    # Remove pytest since skip is gone and no pytest usage remains
    import_block = [l for l in import_block if l != "import pytest"]

    # Add checker import if not present
    # project_dir is 4 levels up: <project>/tests/<service>/<api_group>/<test_folder>/test.py
    project_dir = test.file_path.parents[4]
    checker_import = _checker_import(project_dir, test)
    if not any(checker_fn in l for l in import_block):
        import_block.append(checker_import)

    # Lean test body: call + checker
    func_lines = [
        f"async def {test.function_name}({test.fixture_name}) -> None:",
        call_str,
        f"    {checker_fn}(response)",
    ]

    return (
        "\n".join(import_block).strip()
        + "\n\n\n"
        + "\n".join(func_lines)
        + "\n"
    )


# ── Non-null field discovery ──────────────────────────────────────────────────

def _service_name_from_fixture(fixture_name: str) -> str:
    """
    "cyco_app_assets_api" → "cyco_app"
    Strips known Api suffixes produced by openapi-generator.
    """
    for suffix in ("_api", "_Api"):
        if fixture_name.endswith(suffix):
            fixture_name = fixture_name[: -len(suffix)]
            break
    # Remove the last _word segment (the api-group part like _assets)
    parts = fixture_name.rsplit("_", 1)
    # If what remains looks like a service name (no double underscores) keep it;
    # otherwise return the full name. Simple heuristic: service names don't end with
    # a known api-group word. We just remove the last underscore segment if there is one
    # and the result is still at least 3 chars.
    if len(parts) == 2 and len(parts[0]) >= 3:
        return parts[0]
    return fixture_name


def _get_non_null_fields(
    project_dir: Path,
    service_name: str,
    model_class_name: str | None,
    response_data: dict | list,
    response_type: str,
) -> list[str] | None:
    """
    Dynamically import the Pydantic model produced by `generate` and walk its
    model_fields to produce a list of Python attribute names whose values were
    non-null in the sample response.

    Mapping strategy (JSON key → Python name):
      field.alias (e.g. "attribution-certainty")
        → normalised alias ("attribution_certainty")   ← check raw JSON
        → fallback to Python name directly             ← check raw JSON

    Returns None if the model cannot be loaded (caller skips saving the file).
    """
    if not model_class_name or response_type not in ("pydantic", "list_pydantic"):
        return None

    import sys
    import importlib

    pkg = f"clients.http.{service_name}.models"
    try:
        if pkg not in sys.modules:
            sys.path.insert(0, str(project_dir))
            importlib.import_module(f"clients.http.{service_name}")
            importlib.import_module(pkg)

        ModelClass = getattr(sys.modules[pkg], model_class_name, None)
        if ModelClass is None:
            return None

        sample = response_data[0] if isinstance(response_data, list) else response_data
        if not isinstance(sample, dict):
            return None

        non_null: list[str] = []
        for py_name, field_info in ModelClass.model_fields.items():
            alias = field_info.alias or py_name
            alias_norm = alias.replace("-", "_")
            # Try alias, normalised alias, then python name
            value = sample.get(alias) if alias in sample else (
                sample.get(alias_norm) if alias_norm in sample else
                sample.get(py_name)
            )
            if value is not None:
                non_null.append(py_name)

        return non_null

    except Exception as e:
        print(f"     ⚠️  Could not load model {model_class_name}: {e}")
        return None


_CHECKER_UTILS_CONTENT = '''\
"""
Shared response checker utility.

check_response() reads the non_null_fields.json snapshot saved by fill_skipped
and dynamically asserts that all previously non-null fields are still non-null.

This way checker files stay tiny and work for any model — no hardcoded field names.
"""
import json
from pathlib import Path

from assertpy import assert_that, soft_assertions


def check_response(response, snapshot_dir: Path) -> None:
    """
    Assert that the response structure matches the saved snapshot.

    - response not None
    - for list responses: at least 0 items
    - all fields that were non-null in the sample are still non-null
    """
    non_null_path = snapshot_dir / "non_null_fields.json"

    with soft_assertions():
        assert_that(response).is_not_none()

        is_list = isinstance(response, list)
        if is_list:
            assert_that(len(response)).is_greater_than_or_equal_to(0)

        if not non_null_path.exists():
            return

        non_null_fields: list[str] = json.loads(non_null_path.read_text())
        if not non_null_fields:
            return

        item = response[0] if is_list else response
        item_dict = item.model_dump() if hasattr(item, "model_dump") else item

        for field in non_null_fields:
            assert_that(item_dict.get(field)).described_as(field).is_not_none()
'''


def _ensure_checker_utils(project_dir: Path) -> None:
    """
    Write checkers/utils.py into the project the first time fill_skipped writes a checker.
    Content is embedded inline so this works whether running from project_gen or from
    a copy inside the project's heal/ directory.
    """
    dest = project_dir / "checkers" / "utils.py"
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_CHECKER_UTILS_CONTENT)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _load_auth_headers(project_dir: Path, service_name: str) -> dict:
    """
    Read DEFAULT_HEADERS dict from fixtures/{service_name}.py.
    This gives fill_skipped the same headers the test client uses.
    """
    fixture_file = project_dir / "fixtures" / f"{service_name}.py"
    if not fixture_file.exists():
        return {}
    source = fixture_file.read_text()

    # Parse DEFAULT_HEADERS = { "Key": "value", ... }
    import re
    match = re.search(r"DEFAULT_HEADERS\s*=\s*\{([^}]+)\}", source, re.DOTALL)
    if not match:
        return {}

    headers = {}
    for pair in re.finditer(r'["\']([^"\']+)["\']\s*:\s*["\']([^"\']+)["\']', match.group(1)):
        headers[pair.group(1)] = pair.group(2)
    return headers


# ── UI ────────────────────────────────────────────────────────────────────────

def _confirm_changes(test: SkippedTest, new_source: str, auto: bool) -> bool:
    diff = list(difflib.unified_diff(
        test.source.splitlines(),
        new_source.splitlines(),
        lineterm="",
        n=3,
    ))
    if not diff:
        print("     (no changes)")
        return False

    print("\n" + "─" * 60)
    for line in diff:
        if line.startswith("+"):
            print(f"\033[32m{line}\033[0m")
        elif line.startswith("-"):
            print(f"\033[31m{line}\033[0m")
        else:
            print(line)
    print("─" * 60)

    if auto:
        return True

    try:
        answer = input("     Apply? [y/N/a(ll)] ").strip().lower()
    except EOFError:
        # Not running in an interactive terminal — default to apply
        print("y (stdin not interactive, applying automatically)")
        answer = "y"
    return answer in ("y", "yes", "a", "all")


def _describe_response(data: dict | list) -> str:
    if isinstance(data, list):
        return f"list of {len(data)} items"
    if isinstance(data, dict):
        return f"object with keys: {list(data.keys())[:5]}"
    return str(type(data))


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fill skipped tests with real API data")
    parser.add_argument("--service", "-s", help="Process only this service")
    parser.add_argument("--test", "-t", help="Process only tests matching this name substring")
    parser.add_argument("--auto", "-a", action="store_true", help="Apply all without confirmation")
    args = parser.parse_args()

    run(project_dir=Path.cwd(), service_filter=args.service, test_filter=args.test, auto=args.auto)
