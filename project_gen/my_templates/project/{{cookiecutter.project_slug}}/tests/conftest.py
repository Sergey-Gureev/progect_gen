import os
from pathlib import Path

pytest_plugins = ["clients.fixtures"]

def pytest_configure(config):
    """Ensure consistent working directory for pytest for allure and coverage outputs"""
    current_dir = Path.cwd()

    # Change to project root if running from tests directory
    if current_dir.name == 'tests':
        project_root = current_dir.parent
        os.chdir(project_root)
        print(f'Changed working directory to: {project_root}')