"""Read OpenAPI spec and find endpoint info for a given API method."""
import json
import re
from pathlib import Path

import requests


def load_spec(swagger_url: str) -> dict:
    response = requests.get(swagger_url, timeout=15, verify=False)
    response.raise_for_status()
    return response.json()


def find_endpoint(spec: dict, api_method: str) -> dict | None:
    """
    Match an openapi-generator method name back to a spec path+method.
    e.g. "v1_assets_asset_type_post" → POST /v1/assets/{asset_type}
    """
    # Try matching by operationId first (most reliable)
    for path, path_item in spec.get("paths", {}).items():
        for http_method, operation in path_item.items():
            if http_method not in ("get", "post", "put", "patch", "delete"):
                continue
            op_id = operation.get("operationId", "")
            # openapi-generator converts operationId to snake_case
            if _to_snake(op_id) == api_method:
                return _build_endpoint_info(path, http_method, operation, spec)

    # Fallback: reconstruct path from method name
    # e.g. v1_assets_asset_type_post → POST /v1/assets/{asset_type}
    guessed = _method_name_to_path(api_method)
    if guessed:
        path, http_method = guessed
        path_item = spec.get("paths", {}).get(path, {})
        operation = path_item.get(http_method)
        if operation:
            return _build_endpoint_info(path, http_method, operation, spec)

    return None


def _build_endpoint_info(path: str, http_method: str, operation: dict, spec: dict) -> dict:
    """Collect path, method, summary, parameters, request body schema, response schema."""
    info = {
        "path": path,
        "method": http_method.upper(),
        "summary": operation.get("summary", ""),
        "description": operation.get("description", ""),
        "parameters": [],
        "request_body": None,
        "response_schema": None,
    }

    # Parameters
    for param in operation.get("parameters", []):
        param = _resolve_ref(param, spec)
        schema = _resolve_ref(param.get("schema", {}), spec)
        info["parameters"].append({
            "name": param.get("name"),
            "in": param.get("in"),
            "required": param.get("required", False),
            "description": param.get("description", ""),
            "type": schema.get("type"),
            "enum": schema.get("enum"),
        })

    # Request body
    request_body = operation.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        schema = None
        for media_type in ("application/json", "application/x-www-form-urlencoded"):
            if media_type in content:
                schema = _resolve_ref(content[media_type].get("schema", {}), spec)
                break
        info["request_body"] = _flatten_schema(schema, spec) if schema else None

    # 200/201 response schema
    responses = operation.get("responses", {})
    for status in ("200", "201"):
        if status in responses:
            resp = _resolve_ref(responses[status], spec)
            content = resp.get("content", {})
            for media_type in ("application/json", "text/plain"):
                if media_type in content:
                    schema = _resolve_ref(content[media_type].get("schema", {}), spec)
                    info["response_schema"] = _flatten_schema(schema, spec)
                    break
            break

    return info


def _resolve_ref(obj: dict, spec: dict) -> dict:
    if not isinstance(obj, dict):
        return obj
    ref = obj.get("$ref")
    if not ref:
        return obj
    # e.g. #/components/schemas/Foo
    parts = ref.lstrip("#/").split("/")
    result = spec
    for part in parts:
        result = result.get(part, {})
    return result


def _flatten_schema(schema: dict, spec: dict, depth: int = 0) -> dict:
    """Recursively resolve refs and summarize schema for Claude context."""
    if depth > 3:
        return {"type": "..."}
    schema = _resolve_ref(schema, spec)
    if not schema:
        return {}

    result = {}
    if "type" in schema:
        result["type"] = schema["type"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "description" in schema:
        result["description"] = schema["description"]

    if schema.get("type") == "object" or "properties" in schema:
        props = {}
        for k, v in schema.get("properties", {}).items():
            props[k] = _flatten_schema(v, spec, depth + 1)
        if props:
            result["properties"] = props
        if "required" in schema:
            result["required"] = schema["required"]

    if schema.get("type") == "array" and "items" in schema:
        result["items"] = _flatten_schema(schema["items"], spec, depth + 1)

    return result


def _to_snake(name: str) -> str:
    """Convert CamelCase or PascalCase operationId to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def _method_name_to_path(method_name: str) -> tuple[str, str] | None:
    """Heuristic: v1_assets_asset_type_post → (POST, /v1/assets/{asset_type})."""
    http_methods = ("get", "post", "put", "patch", "delete")
    for m in http_methods:
        if method_name.endswith(f"_{m}"):
            path_parts = method_name[: -(len(m) + 1)].split("_")
            path = "/" + "/".join(path_parts)
            return path, m
    return None
