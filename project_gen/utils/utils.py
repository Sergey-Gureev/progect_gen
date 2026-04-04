import os
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

from cookiecutter.main import cookiecutter


def run_command(command: list[str]) -> str:
    result = subprocess.run(args=command, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}, for command: {''.join(command)}")
        sys.exit(1)
    return result.stdout.strip()


def create_project(template: str | None) -> None:
    template = template or str(pathlib.Path(__file__).parent.parent / "my_templates" / "project")
    project_name = Path.cwd().name
    parent_dir = str(Path.cwd())

    toml_path = Path.cwd() / "testproject.toml"
    saved_toml = toml_path.read_text() if toml_path.exists() else None

    print(f"Creating project: {project_name}")
    cookiecutter(
        template=template,
        no_input=True,
        overwrite_if_exists=True,
        output_dir=parent_dir,
        extra_context={
            "project_name": project_name,
            "repository_name": project_name,
        }
    )

    if saved_toml is not None:
        toml_path.write_text(saved_toml)
        print("Restored existing testproject.toml.")

    print("Project created.")


def setup(template: str = None):
    create_project(template=template)
    print("Next steps:")
    print("  1. Fill in testproject.toml with your API details")
    print("  2. Run: project_gen generate")

def generate_api(
        package_name: str,
        swagger_url: str,
        templates: str = None
):
    templates = templates or str(pathlib.Path(__file__).parent.parent / "my_templates" / "python")
    my_command = [
        "java", "-jar", ".venv/bin/openapi-generator-cli-7.17.0.jar",
        # "openapi-generator",
        "generate", "-i", swagger_url,
        "-g", "python",
        "-o", package_name,
        "--library", "asyncio",
        "--package-name", package_name,
        "--skip-validate-spec"
    ]
    if templates:
        my_command.extend(["-t", templates])
    run_command(my_command)
    _move_files(package_name=package_name)




def _move_files(package_name: str) -> None:
    if os.path.exists(f"clients/http/{package_name}"):
        shutil.rmtree(f"clients/http/{package_name}")

    shutil.move(f"{package_name}/{package_name}", f"clients/http/{package_name}")
    shutil.rmtree(f"{package_name}")
    _replace_imports_in_files(directory=f"clients/http/{package_name}",package_name=package_name)

def _replace_imports_in_files(directory: str, package_name: str) -> None:
    from_search_pattern = f"from {package_name}"
    import_search_pattern = f"import {package_name}"
    replace_pattern = f"clients.http.{package_name}"
    path = pathlib.Path(directory)
    for file_path in path.rglob("*.py"):
        with open(file_path, "r", encoding="utf-8") as file:
            lines = file.readlines()

        updated_lines = []
        for line in lines:
            line = line.replace(from_search_pattern, f"from {replace_pattern}")
            line = line.replace(import_search_pattern, f"import {replace_pattern}")
            line = line.replace(
                f"klass = getattr({package_name}.models, klass)",
                f"klass = getattr(clients.http.{package_name}.models, klass)",
            )
            updated_lines.append(line)
        with open(file_path, "w", encoding="utf-8") as file:
            file.writelines(updated_lines)


