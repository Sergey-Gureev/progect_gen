import ast
from dataclasses import dataclass
from inflection import underscore, camelize
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from jinja2 import Environment, FileSystemLoader

from project_gen.internal.collector import ClientCollector


@dataclass
class MethodInfo:
    client_name: str  # Название клиента (класса)
    method_name: str  # Название метода
    parameters: List[Tuple[str, Optional[str]]]  # Список параметров и их типов


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
        """Упрощает аннотацию типа, убирая лишнюю информацию."""
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
        """Извлекает параметры функции и их типы."""
        parameters = []
        for arg in node.args.args:
            param_name = arg.arg
            param_type = None
            if arg.annotation:
                param_type = self.simplify_annotation(arg.annotation)
            parameters.append((param_name, param_type))
        return parameters

    def parse_class_methods(self, tree: ast.AST, client_name: str) -> List[MethodInfo]:
        """Парсит методы класса и возвращает список MethodInfo."""
        methods_info = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == client_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = item.name

                        # Фильтруем ненужные методы
                        if (
                            method_name.startswith("_")
                            and method_name.endswith("serialize")
                            or method_name.endswith("_with_http_info")
                            or method_name.endswith("_without_preload_content")
                            or method_name == "__init__"
                        ):
                            continue

                        parameters = self.parse_method_parameters(item)
                        methods_info.append(
                            MethodInfo(
                                client_name=client_name,
                                method_name=method_name,
                                parameters=parameters,
                            )
                        )
        return methods_info

    def generate_test_code(self, service_name: str, method_info: MethodInfo) -> str:
        test_template = self.env.get_template("test.jinja2")
        return test_template.render(
            package=service_name,
            client_name=method_info.client_name,
            method_name=method_info.method_name,
            parameters=method_info.parameters,
        )

    def save_test_file(
        self,
        service_name: str,
        api_type: str,
        client_name: str,
        method_info: MethodInfo,
    ):
        test_dir = (
            self.output_dir
            / service_name
            / api_type
            / underscore(client_name)
            / method_info.method_name
        )
        test_dir.mkdir(parents=True, exist_ok=True)

        self.create_init_files(test_dir)

        test_file = test_dir / f"test_{method_info.method_name}.py"

        test_code = self.generate_test_code(service_name, method_info)

        if not test_file.exists():
            print(f"Creating test file: {test_file}")
            test_file.write_text(test_code)

    def create_init_files(self, path: Path):
        init_file = path / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# Auto-generated __init__.py file")

        parent_dir = path.parent
        if parent_dir != self.output_dir:
            self.create_init_files(parent_dir)

    def generate(self):
        client_type: str = "http"
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
                self.save_test_file(package, client_type, client_name, method_info)
