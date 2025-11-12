from pathlib import Path
from tempfile import template

from jinja2 import Environment, FileSystemLoader
from inflection import camelize, underscore


class ClientConnector():
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

class Generator:

    def __init__(self):
        self.clients = ClientConnector().collect_clients()
        self.templates_dir = Path(__file__).parent.parent / "my_templates" / "tests"
        self.env = Environment(loader=FileSystemLoader(self.templates_dir), autoescape=True)
        self.env.filters["underscore"] = underscore
        self.env.filters["camelize"] = camelize

    def generate(self):
        fixture_template = self.env.get_template("fixtures.jinja2")
        fixtures = fixture_template.render(clients=self.clients)
        with open("clients/fixtures.py", "w", encoding="utf-8") as f:
            f.write(fixtures)
        return fixtures


# print(ClientConnector().collect_clients())
# print(Generator().generate())
