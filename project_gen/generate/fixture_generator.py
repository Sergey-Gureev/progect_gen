from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def camelize(string):
    return "".join(word.capitalize() for word in string.split("_"))


def underscore(word):
    result = []
    for i, char in enumerate(word):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result).replace("-", "_")


class ClientCollector:
    base_path: Path = Path(".") / "clients" / "http"

    def collect_clients(self):
        clients = []
        for file_path in self.base_path.rglob("*.py"):
            if str(file_path.parent).endswith("api") and file_path.name.endswith("__init__.py"):
                with file_path.open("r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines:
                    if line.startswith("from"):
                        client = {
                            "client": line.split()[-1],
                            "package": str(file_path.parent.parent).split("/")[-1],
                        }
                        clients.append(client)
        return clients


class FixturesGenerator:

    def __init__(self):
        self.clients = ClientCollector().collect_clients()
        self.templates_dir = Path(__file__).parent.parent / "templates" / "fixtures"
        self.env = Environment(loader=FileSystemLoader(self.templates_dir), autoescape=True)
        self.env.filters["underscore"] = underscore
        self.env.filters["camelize"] = camelize

    def generate(self, services: list[dict]):
        service_map = {s["service_name"]: s for s in services}
        for client in self.clients:
            service = service_map.get(client["package"], {})
            client["host"] = service.get("base_url", "")
            client["relative_path_to_swagger"] = service.get("relative_path_to_swagger", "")

        Path("fixtures").mkdir(exist_ok=True)
        self._write_coverage_utils()
        self._write_config_fixture(services)
        self._write_service_fixtures(services)
        self._update_conftest(services)

        self._update_stg_yaml(services)

        self._generate_coverage_configs(services)

    def _write_config_fixture(self, services: list[dict]) -> None:
        service_names = list(dict.fromkeys(c["package"] for c in self.clients))
        template = self.env.get_template("fixtures_config.jinja2")
        with open("fixtures/config.py", "w", encoding="utf-8") as f:
            f.write(template.render(packages=service_names))

    def _write_service_fixtures(self, services: list[dict]) -> None:
        template = self.env.get_template("fixtures_service.jinja2")
        for service in services:
            service_name = service["service_name"]
            fixture_path = Path(f"fixtures/{service_name}.py")
            if fixture_path.exists():
                continue  # preserve user edits (api keys, custom auth, etc.)
            service_clients = [c for c in self.clients if c["package"] == service_name]
            fixture_path.write_text(template.render(
                package=service_name,
                host=service["base_url"],
                relative_path_to_swagger=service["relative_path_to_swagger"],
                clients=service_clients,
            ))

    def _update_conftest(self, services: list[dict]) -> None:
        service_names = [s["service_name"] for s in services]
        plugin_lines = '    "fixtures.config",\n' + "".join(
            f'    "fixtures.{name}",\n' for name in service_names
        )
        conftest = (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "pytest_plugins = [\n"
            f"{plugin_lines}"
            "]\n"
            "\n"
            "\n"
            "def pytest_configure(config):\n"
            '    """Ensure consistent working directory for pytest for allure and coverage outputs"""\n'
            "    current_dir = Path.cwd()\n"
            "    if 'tests' in str(current_dir):\n"
            "        project_root = str(current_dir).split('/test')[0]\n"
            "        os.chdir(project_root)\n"
            "        print(f'Changed working directory to: {project_root}')\n"
        )
        with open("tests/conftest.py", "w", encoding="utf-8") as f:
            f.write(conftest)

    def _update_stg_yaml(self, services: list[dict]) -> None:
        """Merge new services into stg.yaml, preserving existing values."""
        import re
        stg_path = Path("config/stg.yaml")
        existing: dict = {}
        if stg_path.exists():
            content = stg_path.read_text()
            # parse simple "  key: value" lines under "service:"
            for m in re.finditer(r'^\s{2}(\w+):\s*"?([^"\n]*)"?', content, re.MULTILINE):
                existing[m.group(1)] = m.group(2).strip()

        lines = ["service:\n"]
        for service in services:
            pkg = service["service_name"]
            # keep existing value if already set and non-empty, else use base_url from toml
            value = existing.get(pkg) or service["base_url"]
            lines.append(f'\n  {pkg}: "{value}"')

        stg_path.write_text("".join(lines) + "\n")

    def _generate_coverage_configs(self, services: list[dict]) -> None:
        import json
        config_dir = Path("config")
        template_config = config_dir / "swagger-coverage-config.json"
        base = json.loads(template_config.read_text()) if template_config.exists() else json.loads(self._default_coverage_config())
        Path("reports/coverage").mkdir(parents=True, exist_ok=True)
        for service in services:
            service_name = service["service_name"]
            config_path = config_dir / f"swagger-coverage-config-{service_name}.json"
            if not config_path.exists():
                service_config = json.loads(json.dumps(base))  # deep copy
                service_config["writers"]["html"]["filename"] = (
                    f"reports/coverage/swagger-coverage-report-{service_name}.html"
                )
                config_path.write_text(json.dumps(service_config, indent=4))

    @staticmethod
    def _default_coverage_config() -> str:
        import json
        return json.dumps({
            "rules": {
                "status": {"enable": True, "ignore": [], "filter": []},
                "paths": {"enable": True, "ignore": []},
                "only-declared-status": {"enable": False},
                "exclude-deprecated": {"enable": True},
            },
            "writers": {
                "html": {"locale": "en", "filename": "swagger-coverage-report.html"},
            },
        }, indent=4)

    @staticmethod
    def _write_coverage_utils() -> None:
        content = (
            "import json\n"
            "import os\n"
            "import re\n"
            "import aiohttp\n"
            "from pathlib import Path\n"
            "from faker import Faker\n"
            "\n"
            "\n"
            "def _detect_spec_version(api_name: str) -> str:\n"
            '    """Detect OpenAPI version from the downloaded swagger file."""\n'
            "    for base in [Path(\"reports/specs\"), Path(\"results/swagger-coverage\"), Path(\".\")]:\n"
            "        swagger_file = base / f\"swagger-{api_name}.json\"\n"
            "        if swagger_file.exists():\n"
            "            try:\n"
            "                spec = json.loads(swagger_file.read_text())\n"
            "                if \"openapi\" in spec:\n"
            "                    return spec[\"openapi\"]\n"
            "                if \"swagger\" in spec:\n"
            "                    return spec[\"swagger\"]\n"
            "            except Exception:\n"
            "                pass\n"
            "    return \"2.0\"\n"
            "\n"
            "\n"
            "def _build_coverage_schema(host: str, path: str, method: str, status: int, version: str, params) -> dict:\n"
            '    """Build a coverage schema dict in the correct format for the detected spec version."""\n'
            "    if version.startswith(\"3\"):\n"
            "        return {\n"
            "            \"openapi\": version,\n"
            "            \"info\": {\"title\": host, \"version\": \"1.0.0\"},\n"
            "            \"paths\": {\n"
            "                path: {\n"
            "                    method: {\n"
            "                        \"parameters\": [],\n"
            "                        \"responses\": {str(status): {\"description\": str(status)}}\n"
            "                    }\n"
            "                }\n"
            "            }\n"
            "        }\n"
            "    else:\n"
            "        scheme = re.match(r\"(\\w+)://\", host).group(1)\n"
            "        return {\n"
            "            \"swagger\": \"2.0\",\n"
            "            \"host\": host,\n"
            "            \"schemes\": [scheme],\n"
            "            \"consumes\": [params.headers.get(\"content-type\", \"application/json\")],\n"
            "            \"produces\": [params.response.headers.get(\"content-type\", \"application/json\")],\n"
            "            \"paths\": {\n"
            "                path: {\n"
            "                    method: {\n"
            "                        \"parameters\": [],\n"
            "                        \"responses\": {status: {}}\n"
            "                    }\n"
            "                }\n"
            "            }\n"
            "        }\n"
            "\n"
            "\n"
            "def _coverage_trace(host: str, output_dir: str, api_name: str) -> aiohttp.TraceConfig:\n"
            '    """aiohttp TraceConfig that writes swagger-coverage files for each request."""\n'
            "    trace = aiohttp.TraceConfig()\n"
            "\n"
            "    async def on_request_end(session, ctx, params):\n"
            "        try:\n"
            "            url = str(params.url)\n"
            "            method = params.method.lower()\n"
            "            response = params.response\n"
            "            path = re.sub(f\"^{re.escape(host.rstrip('/'))}\", \"\", url.split(\"?\")[0]) or \"/\"\n"
            "\n"
            "            version = _detect_spec_version(api_name)\n"
            "            schema = _build_coverage_schema(host, path, method, response.status, version, params)\n"
            "\n"
            "            os.makedirs(output_dir, exist_ok=True)\n"
            "            rnd = Faker().pystr(min_chars=5, max_chars=5)\n"
            "            file_name = f\"{method.upper()} {path[1:]} ({rnd}).json\".replace(\"/\", \"-\").replace(\":\", \"_\")\n"
            "            with open(f\"{output_dir}/{file_name}\", \"w\") as f:\n"
            "                json.dump(schema, f, indent=4)\n"
            "        except Exception as e:\n"
            "            print(f\"[coverage trace error] {e}\")\n"
            "\n"
            "    trace.on_request_end.append(on_request_end)\n"
            "    return trace\n"
        )
        with open("fixtures/_coverage.py", "w", encoding="utf-8") as f:
            f.write(content)
