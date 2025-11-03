import shutil
import subprocess
from pathlib import Path


def move_directory_contents(src: Path, dst: Path):
    """Move all files and folders from `src` into `dst`."""
    for item in src.iterdir():
        target = dst / item.name
        shutil.move(str(item), str(target))


def init_poetry():
    subprocess.run(["poetry", "install", "--no-root"])

def init_pre_commit():
    subprocess.run(["poetry", "add", "pre_commit"])
    subprocess.run(["poetry", "run", "pre_commit", "install"])
    subprocess.run(["poetry", "run", "pre_commit", "autoupdate"])

if __name__ == "__main__":
    if "{{ cookiecutter.use_current_directory }}".lower() == "y":
        src = Path.cwd().parent
        # assert src.name == "{{ cookiecutter.project_slug }}"
    init_poetry()
    init_pre_commit()