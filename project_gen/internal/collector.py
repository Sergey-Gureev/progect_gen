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
    
class ClientCollector():
    base_path: Path = Path(".") / "clients" / "http"

    def collect_clients(self):
        clients = []
        for file_path in self.base_path.rglob("*.py"):
            if str(file_path.parent).endswith('api') and file_path.name.endswith("__init__.py"):
                with file_path.open('r', encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines:
                    if line.startswith('from'):
                        client = {
                            "client": line.split()[-1],
                            "package":  str(file_path.parent.parent).split('/')[-1]
                        }
                        clients.append(client)
        return clients

class FixturesGenerator:

    def __init__(self):
        self.clients = ClientCollector().collect_clients()
        self.templates_dir = Path(__file__).parent.parent / "my_templates" / "tests"
        self.env = Environment(loader=FileSystemLoader(self.templates_dir), autoescape=True)
        self.env.filters["underscore"] = underscore
        self.env.filters["camelize"] = camelize

    def generate(self, services: list[dict]):
        """
        services: list of dicts with keys: service_name, base_url, relative_path_to_swagger
        """
        service_map = {s["service_name"]: s for s in services}

        for client in self.clients:
            service = service_map.get(client["package"], {})
            client["host"] = service.get("base_url", "")
            client["relative_path_to_swagger"] = service.get("relative_path_to_swagger", "")

        fixture_template = self.env.get_template("fixtures.jinja2")
        stg_env_template = self.env.get_template("stg_env_template.jinja2")

        with open("clients/fixtures.py", "w", encoding="utf-8") as f:
            f.write(fixture_template.render(clients=self.clients))

        with open("config/stg.yaml", "w", encoding="utf-8") as f:
            f.write(stg_env_template.render(clients=self.clients))

        self._generate_coverage_configs(services)

    def _generate_coverage_configs(self, services: list[dict]) -> None:
        template_config = Path("swagger-coverage-config-*.json")
        existing = list(Path(".").glob("swagger-coverage-config-*.json"))
        config_content = existing[0].read_text() if existing else self._default_coverage_config()

        for service in services:
            config_path = Path(f"swagger-coverage-config-{service['service_name']}.json")
            if not config_path.exists():
                config_path.write_text(config_content)

    @staticmethod
    def _default_coverage_config() -> str:
        import json
        return json.dumps({
            "rules": {
                "status": {"enable": True, "ignore": [], "filter": []},
                "paths": {"enable": True, "ignore": []},
                "only-declared-status": {"enable": False},
                "exclude-deprecated": {"enable": True}
            },
            "writers": {
                "html": {"locale": "en", "filename": "swagger-coverage-report.html"}
            }
        }, indent=4)

