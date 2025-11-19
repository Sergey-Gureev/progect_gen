import shutil
from pathlib import Path


def move_directory_contents(src: Path, dst: Path):
    """Move all files and folders from `src` into `dst`."""
    for item in src.iterdir():
        target = dst / item.name
        shutil.move(str(item), str(target))


if __name__ == "__main__":
    if "{{ cookiecutter.use_current_directory }}".lower() == "y":
        src = Path.cwd()
        # assert src.name == "{{ cookiecutter.project_slug }}"
        move_directory_contents(src, src.parent)