"""Parse skipped test files and extract API call context."""
import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestParam:
    name: str
    type_hint: str
    required: bool  # True if value is `...` (Ellipsis)


@dataclass
class SkippedTest:
    file_path: Path
    function_name: str
    fixture_name: str       # e.g. "cyco_app_assets_api"
    api_method: str         # e.g. "v1_assets_asset_type_post"
    params: list[TestParam] = field(default_factory=list)
    source: str = ""        # original file content


def _type_hint_to_str(node) -> str:
    if node is None:
        return "Any"
    return ast.unparse(node)


def _is_ellipsis(node) -> bool:
    """Check if node is `...` (Ellipsis — required param marker)."""
    return isinstance(node, ast.Constant) and node.value is ...


def parse_skipped_tests(tests_dir: Path) -> list[SkippedTest]:
    """Find all test files with @pytest.mark.skip and extract their context."""
    results = []
    for test_file in tests_dir.rglob("test_*.py"):
        source = test_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not _has_skip_decorator(node):
                continue

            test = _extract_test_info(node, test_file, source)
            if test:
                results.append(test)

    return results


def _has_skip_decorator(node: ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        # @pytest.mark.skip
        if isinstance(dec, ast.Attribute) and dec.attr == "skip":
            return True
        # @pytest.mark.skip(reason=...)
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Attribute) and func.attr == "skip":
                return True
    return False


def _extract_test_info(node: ast.AsyncFunctionDef, file_path: Path, source: str) -> SkippedTest | None:
    """Extract fixture name, API method call, and params from a test function."""
    # Get fixture name from first argument (e.g. cyco_app_assets_api)
    if not node.args.args:
        return None
    fixture_name = node.args.args[0].arg

    # Find the API method call: await fixture_name.some_method(...)
    api_method = None
    call_node = None
    for child in ast.walk(node):
        if not isinstance(child, ast.Await):
            continue
        call = child.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == fixture_name):
            continue
        api_method = func.attr
        call_node = call
        break

    if not api_method:
        return None

    # Extract annotated assignments as params (name: Type = value)
    params = []
    for child in node.body:
        if not isinstance(child, ast.AnnAssign):
            continue
        if not isinstance(child.target, ast.Name):
            continue
        name = child.target.id
        type_hint = _type_hint_to_str(child.annotation)
        required = child.value is None or _is_ellipsis(child.value)
        params.append(TestParam(name=name, type_hint=type_hint, required=required))

    return SkippedTest(
        file_path=file_path,
        function_name=node.name,
        fixture_name=fixture_name,
        api_method=api_method,
        params=params,
        source=source,
    )
