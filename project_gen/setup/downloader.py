import os
import platform
import shutil
from pathlib import Path

import requests

OPENAPI_GENERATOR = "openapi-generator-cli-7.17.0.jar"
JAR_DIR = Path(".bin")


def download_openapi_generator() -> None:
    url = (
        "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/"
        "7.17.0/openapi-generator-cli-7.17.0.jar"
    )
    local_path = Path.cwd() / OPENAPI_GENERATOR

    print(f"📂 Current working dir: {Path.cwd()}")
    print(f"⬇️ Downloading to: {local_path}")

    with requests.get(url, stream=True, timeout=100, verify=False) as response:
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    if platform.system() != "Windows":
        os.chmod(local_path, 0o755)

    jar_dir = Path.cwd() / JAR_DIR
    jar_dir.mkdir(parents=True, exist_ok=True)

    destination = jar_dir / OPENAPI_GENERATOR
    print(f"📦 Moving {local_path} → {destination}")
    shutil.move(str(local_path), str(destination))
    print(f"✅ Done! File now at: {destination}")


def ensure_openapi_generator() -> None:
    if not (Path.cwd() / JAR_DIR / OPENAPI_GENERATOR).exists():
        download_openapi_generator()
    print(f"Downloaded {OPENAPI_GENERATOR}")
