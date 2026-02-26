from pathlib import Path
from tempfile import template

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

    def generate(self, base_url, relative_path_to_swagger):
        fixture_template = self.env.get_template("fixtures.jinja2")
        stg_env_template = self.env.get_template("stg_env_template.jinja2")
        for client in self.clients:
            client['host'] = base_url
            client['relative_path_to_swagger'] = relative_path_to_swagger
        fixtures = fixture_template.render(clients=self.clients)
        with open("clients/fixtures.py", "w", encoding="utf-8") as f:
            f.write(fixtures)

        fixtures = stg_env_template.render(clients=self.clients)
        with open("config/stg.yaml", "w", encoding="utf-8") as f:
            f.write(fixtures)

