import os
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path


def run_command(command: list[str]) -> str:
    result = subprocess.run(args=command, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}, for command: {' '.join(command)}")
        sys.exit(1)
    return result.stdout.strip()


def generate_client(package_name: str, swagger_url: str, templates: str | None = None) -> None:
    templates = templates or str(pathlib.Path(__file__).parent.parent / "templates" / "openapi")
    command = [
        "java", "-jar", ".venv/bin/openapi-generator-cli-7.17.0.jar",
        "generate", "-i", swagger_url,
        "-g", "python",
        "-o", package_name,
        "--library", "asyncio",
        "--package-name", package_name,
        "--skip-validate-spec",
        "-t", templates,
    ]
    run_command(command)
    _move_client_files(package_name=package_name)


def _move_client_files(package_name: str) -> None:
    if os.path.exists(f"clients/http/{package_name}"):
        shutil.rmtree(f"clients/http/{package_name}")

    shutil.move(f"{package_name}/{package_name}", f"clients/http/{package_name}")
    shutil.rmtree(f"{package_name}")
    _fix_imports(directory=f"clients/http/{package_name}", package_name=package_name)


def _fix_imports(directory: str, package_name: str) -> None:
    from_prefix = f"from {package_name}"
    import_prefix = f"import {package_name}"
    client_path = f"clients.http.{package_name}"

    for file_path in pathlib.Path(directory).rglob("*.py"):
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = []
        for line in lines:
            line = line.replace(from_prefix, f"from {client_path}")
            line = line.replace(import_prefix, f"import {client_path}")
            line = line.replace(
                f"klass = getattr({package_name}.models, klass)",
                f"klass = getattr(clients.http.{package_name}.models, klass)",
            )
            updated.append(line)

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(updated)
