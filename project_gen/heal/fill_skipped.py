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

from project_gen.heal.test_parser import parse_skipped_tests, SkippedTest, TestParam
from project_gen.heal.spec_reader import load_spec, find_endpoint


# ── Entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path, service_filter: str | None = None, auto: bool = False) -> None:
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

    # Resolve parameter values
    param_values, missing = _resolve_params(test.params, endpoint)
    if missing:
        print(f"     ⚠️  Cannot fill required params without enum: {missing}")
        print(f"     ⚠️  Skipping — fill these manually in the test")
        return

    # Make real request
    print(f"     📡 Requesting with params: {param_values}")
    try:
        response_data = asyncio.run(
            _real_request(base_url, endpoint, param_values, headers)
        )
    except Exception as e:
        print(f"     ⚠️  Request failed: {e}")
        return

    if response_data is None:
        print(f"     ⚠️  Got empty or error response, skipping")
        return

    print(f"     ✅ Got response ({_describe_response(response_data)})")

    # Save to expected_result/
    snapshot_path = _save_snapshot(
        project_dir, test, endpoint, param_values, response_data
    )
    print(f"     💾 Saved to {snapshot_path.relative_to(project_dir)}")

    # Generate new test source
    new_source = _generate_test(test, endpoint, param_values, response_data)

    # Show diff and ask for confirmation
    if _confirm_changes(test, new_source, auto):
        test.file_path.write_text(new_source)
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


# ── Real HTTP request ─────────────────────────────────────────────────────────

async def _real_request(
    base_url: str,
    endpoint: dict,
    param_values: dict,
    headers: dict,
) -> dict | list | None:
    method = endpoint["method"].upper()
    path = endpoint["path"]

    # Fill path params: /v1/assets/{asset_type} → /v1/assets/ip
    for name, value in param_values.items():
        path = path.replace(f"{{{name}}}", str(value))
        # also try kebab-case key in path
        path = path.replace(f"{{{name.replace('_', '-')}}}", str(value))

    url = base_url.rstrip("/") + path

    # Remaining values go to query params (for GET/POST query style)
    query_params = {
        k: v for k, v in param_values.items()
        if f"{{{k}}}" not in endpoint["path"]
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        req_kwargs: dict = {"ssl": False}
        if query_params:
            req_kwargs["params"] = query_params

        # For POST with no request body — send empty JSON
        if method == "POST" and not endpoint.get("request_body"):
            req_kwargs["json"] = {}

        async with session.request(method, url, **req_kwargs) as resp:
            if resp.status in (200, 201):
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    return {"_raw_text": text}
            else:
                text = await resp.text()
                print(f"     ⚠️  HTTP {resp.status}: {text[:200]}")
                return None


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

def _generate_test(
    test: SkippedTest,
    endpoint: dict,
    param_values: dict,
    response_data: dict | list,
) -> str:
    response_type = endpoint["response_type"]
    assertions = _build_assertions(response_data, response_type)

    # Build the API call with filled params
    call_args = []
    for tp in test.params:
        if tp.name in param_values:
            val = param_values[tp.name]
            call_args.append(f'        {tp.name}="{val}"' if isinstance(val, str) else f'        {tp.name}={val}')
        elif not tp.required:
            call_args.append(f"        {tp.name}=None")

    call_str = (
        f"    response = await {test.fixture_name}.{test.api_method}(\n"
        + ",\n".join(call_args)
        + "\n    )"
    )

    # Rebuild imports — keep originals, add assertpy if needed
    lines = test.source.splitlines()
    import_block = []
    rest_lines = []
    in_imports = True
    for line in lines:
        if in_imports and (line.startswith("import ") or line.startswith("from ") or line == ""):
            import_block.append(line)
        else:
            in_imports = False
            rest_lines.append(line)

    # Add assertpy import if not present
    if not any("assertpy" in l for l in import_block):
        import_block.append("from assertpy import assert_that, soft_assertions")

    # Rebuild function: remove skip decorator, replace body
    func_lines = [
        f"async def {test.function_name}({test.fixture_name}) -> None:",
        call_str,
        "",
        *assertions,
    ]

    return (
        "\n".join(import_block).strip()
        + "\n\n\n"
        + "\n".join(func_lines)
        + "\n"
    )


def _build_assertions(response_data: dict | list, response_type: str) -> list[str]:
    """
    Build assertion lines based on response_type and actual response data.
    Uses soft_assertions so all failures are reported at once.
    Non-null fields → assert is_not_none()
    Null fields     → assert is_none() wrapped as optional (commented hint)
    """
    lines = ["    with soft_assertions():"]

    if response_type == "none":
        lines.append("        assert_that(response).is_none()")
        return lines

    lines.append("        assert_that(response).is_not_none()")

    if response_type in ("list_pydantic", "list_dict"):
        lines.append("        assert_that(len(response)).is_greater_than_or_equal_to(0)")
        if not response_data:
            return lines
        item = response_data[0] if isinstance(response_data, list) else response_data
        accessor = "response[0]"
    else:
        item = response_data
        accessor = "response"

    if not isinstance(item, dict):
        return lines

    for key, value in item.items():
        if key.startswith("_"):
            continue  # internal fields
        attr = f"{accessor}.{key}" if response_type in ("pydantic", "list_pydantic") else f'{accessor}["{key}"]'
        if value is not None:
            lines.append(f"        assert_that({attr}).is_not_none()")
        else:
            lines.append(f"        # {attr} was null in sample — assert_that({attr}).is_none()")

    return lines


# ── Auth ──────────────────────────────────────────────────────────────────────

def _load_auth_headers(project_dir: Path, service_name: str) -> dict:
    """
    Read auth token from fixtures/{service_name}.py if present.
    Looks for header_value="..." in the api_client fixture.
    """
    fixture_file = project_dir / "fixtures" / f"{service_name}.py"
    if not fixture_file.exists():
        return {}
    source = fixture_file.read_text()
    import re
    match = re.search(r'header_name=["\']([^"\']+)["\'].*?header_value=["\']([^"\']+)["\']', source, re.DOTALL)
    if match:
        return {match.group(1): match.group(2)}
    return {}


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

    answer = input("     Apply? [y/N/a(ll)] ").strip().lower()
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
    parser.add_argument("--auto", "-a", action="store_true", help="Apply all without confirmation")
    args = parser.parse_args()

    run(project_dir=Path.cwd(), service_filter=args.service, auto=args.auto)
