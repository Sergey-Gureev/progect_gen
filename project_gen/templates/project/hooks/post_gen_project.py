import shutil
from pathlib import Path


def move_directory_contents(src: Path, dst: Path):
    """Move all files and folders from `src` into `dst`."""
    for item in src.iterdir():
        target = dst / item.name

        if target.exists():
            if target.is_dir():
                # Merge directories
                shutil.copytree(item, target, dirs_exist_ok=True)
                shutil.rmtree(item)
            else:
                # Overwrite file
                shutil.move(str(item), str(target))
        else:
            # Just move it
            shutil.move(str(item), str(target))

    # Remove empty source directory
    src.rmdir()

if __name__ == "__main__":
    if "{{ cookiecutter.use_current_directory }}".lower() == "y":
        src = Path.cwd()
        print('cur path test sergey:', src)
        # assert src.name == "{{ cookiecutter.project_slug }}"
        move_directory_contents(src, src.parent)