# project_gen

CLI-tool for generating API test projects from OpenAPI/Swagger specs.

## Requirements

- Python 3.10+
- Java (for openapi-generator)
- [Poetry](https://python-poetry.org/docs/#installation) — for managing test project dependencies

## Setup a new project

### 0. Create a remote repository

**Option A — DevOps or teammate created it for you:**

```bash
git clone https://github.com/your-org/component_name_api_automation.git
cd component_name_api_automation
```

**Option B — Create it yourself via GitHub CLI:**

```bash
# Install gh if needed: https://cli.github.com

# Organization (replace your-org with the actual org name):
gh repo create your-org/component_name_api_automation --private --clone

cd component_name_api_automation
```

**Option C — Create it yourself via GitHub UI:**

1. Go to github.com → New repository
2. Set name, visibility → Create
3. Clone it locally:

```bash
git clone https://github.com/your-org/component_name_api_automation.git
cd component_name_api_automation
```

### 1. Install Poetry (if not installed)

```bash
pip install poetry
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 3. Install project_gen

```bash
pip install git+https://github.com/Sergey-Gureev/progect_gen.git
```

### 4. Generate project structure

```bash
project_gen setup
```

This creates the folder structure, `testproject.toml`, and `pyproject.toml` with all test dependencies.

### 5. Fill in testproject.toml

```toml
[[http]]
service_name = "component_name"
swagger_url = "https://api.example.com/v1/docs/openapi.json"
base_url = "https://api.example.com"
relative_path_to_swagger = "/v1/docs/openapi.json"
```

Add one `[[http]]` block per service. If one host exposes multiple swagger documents (e.g. `/swagger/Game/swagger.json`, `/swagger/Account/swagger.json`), treat each as a separate service:

```toml
[[http]]
service_name = "game_service"
swagger_url = "http://host/swagger/Game/swagger.json"
base_url = "http://host"
relative_path_to_swagger = "/swagger/Game/swagger.json"

[[http]]
service_name = "account_service"
swagger_url = "http://host/swagger/Account/swagger.json"
base_url = "http://host"
relative_path_to_swagger = "/swagger/Account/swagger.json"
```

### 6. Install project dependencies

```bash
poetry install
```

This installs all dependencies from `pyproject.toml` into the venv.

### 7. Generate clients and tests

```bash
project_gen generate
```

This will:
- Download openapi-generator (first run only)
- Generate API clients into `clients/http/`
- Generate test stubs into `tests/`
- Generate `config/stg.yaml` with environment config

### 8. After generation

- Review and fill in `config/stg.yaml` with real environment values
- Remove `@pytest.mark.skip` from tests you want to run
- Commit the generated code as your baseline:

```bash
git add .
git commit -m "init: generated from swagger"
```

## Running tests

```bash
# Run all tests
pytest tests/

# Run tests for a specific service
pytest tests/cyco_app/

# Run a single test
pytest tests/cyco_app/http/users_api/v1_users_get/test_v1_users_get.py
```

## Allure reports

Install Allure CLI once (requires Java):

```bash
# Mac
brew install allure

# Other — see https://allurereport.org/docs/install/
```

Test results are written to `allure-results/` automatically on every run (configured in `pyproject.toml`).

```bash
# Open interactive report in browser
allure serve allure-results

# Or generate static HTML report
allure generate allure-results -o allure-report --clean
allure open allure-report
```

## Known issues

### `models/json_any.py` — catch-all for complex dictionaries

Some swagger specs generate a `models/json_any.py` that fails to deserialize complex nested objects.
If you see deserialization errors at runtime, open `clients/http/<service_name>/models/json_any.py` and add:

```python
"Dict[str, None]",  # catch-all for complex dictionaries
```

This is a limitation of the openapi-generator Python template, not of project_gen itself.
