import pathlib
from pathlib import Path

from cookiecutter.main import cookiecutter


def create_project(template: str | None = None) -> None:
    template = template or str(pathlib.Path(__file__).parent.parent / "templates" / "project")
    project_name = Path.cwd().name
    parent_dir = str(Path.cwd())

    # Preserve user-edited files across repeated setup runs
    preserved = {
        p: p.read_text()
        for p in [
            Path.cwd() / "testproject.toml",
            Path.cwd() / "config" / "stg.yaml",
        ]
        if p.exists()
    }

    print(f"Creating project: {project_name}")
    cookiecutter(
        template=template,
        no_input=True,
        overwrite_if_exists=True,
        output_dir=parent_dir,
        extra_context={
            "project_name": project_name,
            "repository_name": project_name,
        },
    )

    for path, content in preserved.items():
        path.write_text(content)
        print(f"Restored existing {path.name}.")

    print("Project created.")
    print("Next steps:")
    print("  1. Fill in testproject.toml with your API details")
    print("  2. Run: project_gen generate")
