import toml

from project_gen.utils.download import init
from project_gen.utils.utils import generate_api, setup
import click

@click.group()
def cli() -> None:
    pass

@click.command("generate")
def generate_command(templates: any = None) -> None:
    with open("testproject.toml", 'r') as config_file:
        config = toml.load(config_file)
        print(config)
        for http_service in config['http']:
            package_name = http_service["service_name"]
            swagger_url = http_service["swagger_url"]
            generate_api(
                package_name=package_name,
                swagger_url=swagger_url,
                templates=templates
            )



@click.command("setup")
@click.option("--template", "-t", required=False, default=None)
def setup_command(template) -> None:
    init()
    setup(template=template)

cli.add_command(generate_command)
cli.add_command(setup_command)


if __name__ == "__main__":
    cli()
    # package_name="common"
    # download_codegen()
    # create_project(package_name=package_name)


