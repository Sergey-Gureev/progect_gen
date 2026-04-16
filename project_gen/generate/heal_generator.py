"""
Copy the heal/ tooling into the generated project so it can be run
directly without project_gen installed.

After generate, users run:
    python3 -m heal.fill_skipped --auto
from inside the project directory.
"""
import shutil
from pathlib import Path


_HEAL_DIR = Path(__file__).parent.parent / "heal"

_FILES_TO_COPY = [
    "fill_skipped.py",
    "spec_reader.py",
    "test_parser.py",
]


def copy_heal_to_project(project_dir: Path) -> None:
    dest_dir = project_dir / "heal"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for filename in _FILES_TO_COPY:
        src = _HEAL_DIR / filename
        if src.exists():
            shutil.copy2(src, dest_dir / filename)

    init_file = dest_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text("")

    print(f"heal/ copied to {dest_dir.relative_to(project_dir)}/")
