import click
import toml

from project_gen.generate.fixture_generator import FixturesGenerator
from project_gen.generate.test_generator import TestsGenerator
from project_gen.generate.client_generator import generate_client
from project_gen.setup.downloader import ensure_openapi_generator
from project_gen.setup.project_setup import create_project
from project_gen.heal.healer import TestHealer


@click.group()
def cli() -> None:
    pass


@click.command("setup")
@click.option("--template", "-t", required=False, default=None)
def setup_command(template) -> None:
    ensure_openapi_generator()
    create_project(template=template)


@click.command("generate")
def generate_command() -> None:
    with open("testproject.toml", "r") as config_file:
        config = toml.load(config_file)

    if not config.get("http"):
        print("No http services found in testproject.toml. Please fill it in.")
        return

    services = []
    for http_service in config["http"]:
        generate_client(
            package_name=http_service["service_name"],
            swagger_url=http_service["swagger_url"],
        )
        services.append({
            "service_name": http_service["service_name"],
            "base_url": http_service["base_url"],
            "relative_path_to_swagger": http_service["relative_path_to_swagger"],
        })

    FixturesGenerator().generate(services=services)
    print("Done. Check/fill config/stg.yaml file.")
    TestsGenerator().generate()


@click.command("heal")
@click.option("--service", "-s", default=None, help="Heal only a specific service")
@click.option("--api-key", "-k", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY)")
def heal_command(service, api_key) -> None:
    """Auto-fill skipped tests using Claude + real API responses."""
    from pathlib import Path
    healer = TestHealer(project_dir=Path.cwd(), api_key=api_key)
    healer.heal_all(service_filter=service)


cli.add_command(setup_command)
cli.add_command(generate_command)
cli.add_command(heal_command)


if __name__ == "__main__":
    cli()
