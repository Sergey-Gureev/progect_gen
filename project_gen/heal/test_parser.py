"""
Parse test files and extract skipped test context.

Used by both fill_skipped and heal scripts to understand
what API method each test is calling and with what parameters.
"""
import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestParam:
    name: str
    type_hint: str
    required: bool  # True if value is `...` (Ellipsis — no default provided)


@dataclass
class SkippedTest:
    file_path: Path
    function_name: str
    fixture_name: str   # e.g. "cyco_app_assets_api"
    api_method: str     # e.g. "v1_assets_asset_type_post"
    params: list[TestParam] = field(default_factory=list)
    source: str = ""    # full original file content


def parse_skipped_tests(tests_dir: Path) -> list[SkippedTest]:
    """
    Walk tests_dir recursively, find all async test functions
    decorated with @pytest.mark.skip (but NOT skip(reason=...)).

    We intentionally skip tests with a reason — those were skipped
    by a human on purpose and should not be auto-filled.
    """
    results = []
    for test_file in sorted(tests_dir.rglob("test_*.py")):
        source = test_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not _is_auto_skip(node):
                continue
            test = _extract_test_info(node, test_file, source)
            if test:
                results.append(test)

    return results


def _is_auto_skip(node: ast.AsyncFunctionDef) -> bool:
    """
    Return True only for bare @pytest.mark.skip with no reason argument.
    Ignore @pytest.mark.skip(reason="...") — that's a human decision.
    """
    for dec in node.decorator_list:
        # bare: @pytest.mark.skip
        if isinstance(dec, ast.Attribute) and dec.attr == "skip":
            return True
        # called: @pytest.mark.skip() or @pytest.mark.skip(reason="...")
        if isinstance(dec, ast.Call):
            func = dec.func
            if not (isinstance(func, ast.Attribute) and func.attr == "skip"):
                continue
            # has reason= kwarg or positional arg → human skip, ignore
            if dec.args or dec.keywords:
                return False
            # @pytest.mark.skip() with no args → auto-generated skip
            return True
    return False


def _extract_test_info(
    node: ast.AsyncFunctionDef,
    file_path: Path,
    source: str,
) -> SkippedTest | None:
    """
    Extract fixture name, API method, and parameters from a test function node.

    A test node looks like:
        async def test_retrieve_assets(cyco_app_assets_api: cyco_app.AssetsApi):
            asset_type: StrictStr = ...       ← TestParam(required=True)
            count: Optional[StrictInt] = None ← TestParam(required=False)
            response = await cyco_app_assets_api.v1_assets_asset_type_post(
                asset_type=asset_type, ...
            )
            assert response
    """
    # First argument is the pytest fixture (the API client)
    if not node.args.args:
        return None
    fixture_name = node.args.args[0].arg

    # Find: await fixture_name.some_method(...)
    api_method = _find_api_call(node, fixture_name)
    if not api_method:
        return None

    # Collect annotated variable declarations as test parameters
    params = _collect_params(node)

    return SkippedTest(
        file_path=file_path,
        function_name=node.name,
        fixture_name=fixture_name,
        api_method=api_method,
        params=params,
        source=source,
    )


def _find_api_call(node: ast.AsyncFunctionDef, fixture_name: str) -> str | None:
    """Find the first `await fixture_name.method(...)` and return method name."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Await):
            continue
        call = child.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        # Must be called on the fixture: cyco_app_assets_api.v1_assets_asset_type_post
        if isinstance(func.value, ast.Name) and func.value.id == fixture_name:
            return func.attr
    return None


def _collect_params(node: ast.AsyncFunctionDef) -> list[TestParam]:
    """
    Collect `name: Type = value` statements from the function body.
    required=True when value is `...` (Ellipsis).
    """
    params = []
    for stmt in node.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        type_hint = ast.unparse(stmt.annotation) if stmt.annotation else "Any"
        required = stmt.value is None or (
            isinstance(stmt.value, ast.Constant) and stmt.value.value is ...
        )
        params.append(TestParam(name=name, type_hint=type_hint, required=required))
    return params
