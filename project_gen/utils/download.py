import os
import platform
import shutil
import requests
from pathlib import Path

OPENAPI_GENERATOR = "openapi-generator-cli-7.17.0.jar"
def download_codegen() -> None:
    url = (
        "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/"
        "7.17.0/openapi-generator-cli-7.17.0.jar"
    )
    file_name = OPENAPI_GENERATOR
    local_path = Path.cwd() / file_name  # absolute path for clarity

    print(f"📂 Current working dir: {Path.cwd()}")
    print(f"⬇️ Downloading to: {local_path}")

    with requests.get(url, stream=True, timeout=100, verify=False) as response:
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    if platform.system() != "Windows":
        os.chmod(local_path, 0o755)

    # Correct venv bin path
    venv_bin = Path(".venv") / ("Scripts" if platform.system() == "Windows" else "bin")
    venv_bin = Path.cwd() / venv_bin  # make it absolute
    venv_bin.mkdir(parents=True, exist_ok=True)

    destination = venv_bin / file_name
    print(f"📦 Moving {local_path} → {destination}")
    shutil.move(str(local_path), str(destination))

    print(f"✅ Done! File now at: {destination}")
    print(f"Exists? {destination.exists()}")

def init()-> None:
    if not os.path.exists(f".venv/bin/{OPENAPI_GENERATOR}"):
        download_codegen()
    print(f"Downloaded {OPENAPI_GENERATOR}")
