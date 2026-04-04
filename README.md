# project_gen

CLI-tool for generating API test projects from OpenAPI/Swagger specs.

## Requirements

- Python 3.10+
- Java (for openapi-generator)

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
# Personal account:
gh repo create component_name_api_automation --private --clone

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

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 2. Install project_gen

```bash
pip install git+https://github.com/Sergey-Gureev/progect_gen.git
```

### 3. Generate project structure

```bash
project_gen setup
```

This creates the folder structure and a `testproject.toml` config file.

### 4. Fill in testproject.toml

```toml
[[http]]
service_name = "my_service"
swagger_url = "https://api.example.com/v1/docs/openapi.json"
base_url = "https://api.example.com"
relative_path_to_swagger = "/v1/docs/openapi.json"
```

Add one `[[http]]` block per service.

### 5. Generate clients and tests

```bash
project_gen generate
```

This will:
- Download openapi-generator (first run only)
- Generate API clients into `clients/http/`
- Generate test stubs into `tests/`
- Generate `config/stg.yaml` with environment config

### 6. After generation

- Review and fill in `config/stg.yaml` with real environment values
- Remove `@pytest.mark.skip` from tests you want to run
- Commit the generated code as your baseline:

```bash
git add .
git commit -m "init: generated from swagger"
```

## Known issues

In `models/json_any.py` you may need to add manually:
```python
"Dict[str, None]",  # catch-all for complex dictionaries
```
