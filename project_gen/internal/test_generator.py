import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from jinja2 import Environment, FileSystemLoader

from project_gen.internal.collector import ClientCollector, underscore, camelize


def _description_to_name(text: str) -> Optional[str]:
    """Convert a human-readable description to a snake_case identifier."""
    if not text:
        return None
    text = text.strip().split("\n")[0]  # first line only
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text).strip("_")
    return text or None


@dataclass
class MethodInfo:
    client_name: str
    method_name: str
    parameters: List[Tuple[str, Optional[str]]]
    description_name: Optional[str] = field(default=None)


class TestsGenerator:
    def __init__(self):
        self.output_dir = Path("tests")
        self.clients_path = Path("clients") / "http"
        self.templates_dir = Path(__file__).parent.parent / "my_templates" / "tests"
        self.env = Environment(
            loader=FileSystemLoader(self.templates_dir), autoescape=True
        )
        self.env.filters["underscore"] = underscore
        self.env.filters["camelize"] = camelize

    def simplify_annotation(self, annotation: ast.AST) -> str:
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                if annotation.value.id == "Optional":
                    return f"Optional[{self.simplify_annotation(annotation.slice)}]"
                elif annotation.value.id == "Union":
                    return f"Union[{self.simplify_annotation(annotation.slice)}]"
                elif annotation.value.id == "Annotated":
                    if isinstance(annotation.slice, ast.Tuple):
                        return self.simplify_annotation(annotation.slice.elts[0])
            elif isinstance(annotation.value, ast.Attribute):
                if annotation.value.attr == "Optional":
                    return f"Optional[{self.simplify_annotation(annotation.slice)}]"
        elif isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return annotation.value
        elif isinstance(annotation, ast.Tuple):
            return ", ".join(
                str(self.simplify_annotation(el)) for el in annotation.elts
            )
        return "Unknown"

    def parse_method_parameters(
        self, node: ast.FunctionDef
    ) -> List[Tuple[str, Optional[str]]]:
        parameters = []
        for arg in node.args.args:
            param_name = arg.arg
            param_type = None
            if arg.annotation:
                param_type = self.simplify_annotation(arg.annotation)
            parameters.append((param_name, param_type))
        return parameters

    def parse_class_methods(self, tree: ast.AST, client_name: str) -> List[MethodInfo]:
        methods_info = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == client_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = item.name
                        if (
                            method_name.startswith("_")
                            and method_name.endswith("serialize")
                            or method_name.endswith("_with_http_info")
                            or method_name.endswith("_without_preload_content")
                            or method_name == "__init__"
                        ):
                            continue

                        parameters = self.parse_method_parameters(item)
                        docstring = ast.get_docstring(item) or ""
                        description_name = _description_to_name(docstring)

                        methods_info.append(
                            MethodInfo(
                                client_name=client_name,
                                method_name=method_name,
                                parameters=parameters,
                                description_name=description_name,
                            )
                        )
        return methods_info

    def generate_test_code(self, service_name: str, method_info: MethodInfo) -> str:
        test_template = self.env.get_template("test.jinja2")
        return test_template.render(
            package=service_name,
            client_name=method_info.client_name,
            method_name=method_info.method_name,
            description_name=method_info.description_name,
            parameters=method_info.parameters,
        )

    def save_test_file(self, service_name: str, client_name: str, method_info: MethodInfo):
        folder_name = method_info.description_name or method_info.method_name
        test_dir = (
            self.output_dir
            / service_name
            / underscore(client_name)
            / folder_name
        )
        test_dir.mkdir(parents=True, exist_ok=True)
        self.create_init_files(test_dir)

        test_file = test_dir / f"test_{folder_name}.py"
        test_code = self.generate_test_code(service_name, method_info)

        if not test_file.exists():
            print(f"Creating test file: {test_file}")
            test_file.write_text(test_code)

    def create_init_files(self, path: Path):
        init_file = path / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        parent_dir = path.parent
        if parent_dir != self.output_dir:
            self.create_init_files(parent_dir)

    def generate(self):
        for client in ClientCollector().collect_clients():
            package = client["package"]
            client_name = client["client"]
            file_path = (
                self.clients_path / package / "api" / f"{underscore(client_name)}.py"
            )

            if not file_path.exists():
                continue

            with file_path.open() as f:
                source_code = f.read()
                tree = ast.parse(source_code)
                methods_info = self.parse_class_methods(tree, client_name)

            for method_info in methods_info:
                self.save_test_file(package, client_name, method_info)
