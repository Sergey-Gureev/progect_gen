import toml

from project_gen.internal.collector import FixturesGenerator
from project_gen.internal.test_generator import TestsGenerator
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
        assert(config is not {}, "update testproject.toml file with desired api")
        for http_service in config['http']:
            package_name = http_service["service_name"]
            swagger_url = http_service["swagger_url"]
            base_url = http_service["base_url"]
            relative_path_to_swagger = http_service["relative_path_to_swagger"]

            generate_api(
                package_name=package_name,
                swagger_url=swagger_url,
                templates=templates
            )
            print("project_gen.main: base_url: ", base_url)
            FixturesGenerator().generate(base_url=base_url, relative_path_to_swagger=relative_path_to_swagger)
            print("check/fill config/stg.yaml file")
    TestsGenerator().generate()


@click.command("setup")
@click.option("--template", "-t", required=False, default=None)
def setup_command(template) -> None:
    init()
    setup(template=template)


cli.add_command(generate_command)
cli.add_command(setup_command)


if __name__ == "__main__":
    cli()

