"""Orchestrate healing of skipped tests."""
import json
import os
from pathlib import Path

import aiohttp
import anthropic
import toml

from project_gen.heal.test_parser import parse_skipped_tests, SkippedTest
from project_gen.heal.spec_reader import load_spec, find_endpoint
from project_gen.heal.claude_generator import generate_healed_test


class TestHealer:
    def __init__(self, project_dir: Path, api_key: str | None = None):
        self.project_dir = project_dir
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY env variable is required for heal")
        self.claude = anthropic.Anthropic(api_key=self.api_key)

    def heal_all(self, service_filter: str | None = None) -> None:
        config = toml.load(self.project_dir / "testproject.toml")
        services = config.get("http", [])

        if not services:
            print("No services in testproject.toml")
            return

        # Load all specs upfront
        specs = {}
        for svc in services:
            name = svc["service_name"]
            if service_filter and name != service_filter:
                continue
            print(f"📥 Loading spec for {name}...")
            try:
                specs[name] = (load_spec(svc["swagger_url"]), svc)
            except Exception as e:
                print(f"  ⚠️  Could not load spec for {name}: {e}")

        # Find skipped tests
        tests_dir = self.project_dir / "tests"
        skipped = parse_skipped_tests(tests_dir)

        if not skipped:
            print("✅ No skipped tests found.")
            return

        print(f"🔍 Found {len(skipped)} skipped tests")

        for test in skipped:
            self._heal_test(test, specs)

    def _heal_test(self, test: SkippedTest, specs: dict) -> None:
        print(f"\n🔧 Healing: {test.file_path.relative_to(self.project_dir)}")

        # Match test to service by fixture name prefix
        service_name = self._detect_service(test.fixture_name, specs)
        if not service_name:
            print(f"  ⚠️  Could not detect service for fixture '{test.fixture_name}', skipping")
            return

        spec, svc_config = specs[service_name]

        # Find endpoint in spec
        endpoint_info = find_endpoint(spec, test.api_method)
        if not endpoint_info:
            print(f"  ⚠️  Could not find endpoint for '{test.api_method}' in spec, skipping")
            return

        print(f"  📌 {endpoint_info['method']} {endpoint_info['path']} — {endpoint_info['summary']}")

        # Try to get a real response for GET endpoints
        real_response = None
        if endpoint_info["method"] == "GET":
            real_response = self._try_real_request(
                base_url=svc_config["base_url"],
                path=endpoint_info["path"],
                params=endpoint_info["parameters"],
            )
            if real_response is not None:
                print(f"  📡 Got real response sample")

        # Generate healed test via Claude
        print(f"  🤖 Generating with Claude...")
        healed = generate_healed_test(
            original_source=test.source,
            endpoint_info=endpoint_info,
            real_response=real_response,
            client=self.claude,
        )

        # Write back
        test.file_path.write_text(healed)
        print(f"  ✅ Written: {test.file_path.name}")

    def _detect_service(self, fixture_name: str, specs: dict) -> str | None:
        """e.g. 'cyco_app_assets_api' → 'cyco_app'"""
        for service_name in specs:
            if fixture_name.startswith(service_name):
                return service_name
        return None

    def _try_real_request(self, base_url: str, path: str, params: list[dict]) -> dict | list | None:
        """Make a real GET request if path has no required params."""
        required_path_params = [
            p for p in params
            if p["in"] == "path" and p["required"]
        ]
        if required_path_params:
            return None  # Can't fill path params automatically

        import asyncio
        try:
            return asyncio.run(self._async_get(base_url, path))
        except Exception as e:
            print(f"  ⚠️  Real request failed: {e}")
            return None

    async def _async_get(self, base_url: str, path: str) -> dict | list | None:
        url = base_url.rstrip("/") + path
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
