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
    templates = templates or str(pathlib.Path(__file__).parent.parent / "templates" / "python")
    command = [
        "java", "-jar", ".bin/openapi-generator-cli-7.17.0.jar",
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
    _fix_json_any(directory=f"clients/http/{package_name}")
    _fix_strict_fields(directory=f"clients/http/{package_name}")


_JSON_ANY_VALIDATOR = '''
    @model_validator(mode='before')
    @classmethod
    def coerce_scalar(cls, data):
        """Wrap scalars/None/lists into actual_instance so Pydantic
        can construct JsonAny from any value, not just dicts."""
        if not isinstance(data, dict):
            return {'actual_instance': data}
        return data
'''

_JSON_ANY_IMPORT_PATCH = "from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictBytes, StrictFloat, StrictInt, StrictStr, ValidationError, field_validator, model_validator"
_JSON_ANY_IMPORT_ORIGINAL = "from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictBytes, StrictFloat, StrictInt, StrictStr, ValidationError, field_validator"


def _fix_json_any(directory: str) -> None:
    """
    Patch the generated json_any.py to handle scalars/None/lists inside
    Dict[str, Optional[JsonAny]] and List[Optional[JsonAny]] fields.

    openapi-generator emits these types for free-form JSON values, but Pydantic
    will try to coerce every value to a JsonAny model — which fails for plain
    strings, numbers, and nulls.  Adding a model_validator(mode='before') that
    wraps non-dict values into {'actual_instance': v} fixes this universally for
    all models that use JsonAny without touching any other generated file.
    """
    json_any_path = Path(directory) / "models" / "json_any.py"
    if not json_any_path.exists():
        return

    source = json_any_path.read_text()

    # Already patched
    if "coerce_scalar" in source:
        return

    # 1. Add model_validator to the pydantic import line
    if _JSON_ANY_IMPORT_ORIGINAL in source:
        source = source.replace(_JSON_ANY_IMPORT_ORIGINAL, _JSON_ANY_IMPORT_PATCH)

    # 2. Insert the validator right before __init__
    source = source.replace(
        "\n    def __init__(self, *args, **kwargs)",
        _JSON_ANY_VALIDATOR + "\n    def __init__(self, *args, **kwargs)",
    )

    json_any_path.write_text(source)
    print(f"  ✅ Patched json_any.py (coerce_scalar validator added)")


def _fix_strict_fields(directory: str) -> None:
    """
    Make all bare Strict* fields in generated models Optional[Strict*] = None.

    openapi-generator marks fields as required when the spec says so, but real
    APIs often return null for those fields.  Pydantic v2 strict types reject
    None, causing ValidationError on deserialisation.

    Pattern matched (field declaration without Optional wrapping):
        name: StrictStr = Field(...)
        name: StrictInt = Field(...)   etc.

    Becomes:
        name: Optional[StrictStr] = Field(default=None, ...)
    """
    import re

    models_dir = Path(directory) / "models"
    if not models_dir.exists():
        return

    # Matches:  <spaces>name: StrictXxx = Field(   — no Optional wrapping
    field_re = re.compile(
        r"^(\s+\w+:\s*)(StrictStr|StrictInt|StrictFloat|StrictBool|StrictBytes)"
        r"(\s*=\s*Field\()",
        re.MULTILINE,
    )
    # Ensure Field() gets default=None if not already present
    default_re = re.compile(r"(=\s*Field\()(?!.*default\s*=)", re.DOTALL)

    patched = 0
    for model_file in sorted(models_dir.glob("*.py")):
        source = model_file.read_text()

        new_source = field_re.sub(
            lambda m: m.group(1) + f"Optional[{m.group(2)}]" + m.group(3),
            source,
        )

        # Add default=None to every Field() that now has Optional but no default
        def _add_default(m: re.Match) -> str:
            return m.group(1) + "default=None, "

        new_source = re.sub(
            r"Optional\[Strict\w+\]\s*=\s*Field\((?![^)]*default\s*=)",
            lambda m: m.group(0).replace("Field(", "Field(default=None, ", 1),
            new_source,
        )

        # Allow None in enum field_validators (validator runs even for Optional fields)
        new_source = re.sub(
            r"^(\s+)if value not in set\(",
            r"\1if value is None:\n\1    return value\n\1if value not in set(",
            new_source,
            flags=re.MULTILINE,
        )

        if new_source != source:
            # Ensure Optional is imported
            if "from typing import" in new_source and "Optional" not in new_source.split("from typing import")[1].split("\n")[0]:
                new_source = new_source.replace(
                    "from typing import ",
                    "from typing import Optional, ",
                    1,
                )
            model_file.write_text(new_source)
            patched += 1

    if patched:
        print(f"  ✅ Made Strict* fields Optional in {patched} model files")


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
