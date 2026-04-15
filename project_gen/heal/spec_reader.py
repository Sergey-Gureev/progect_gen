"""
Read OpenAPI spec and find endpoint info for a given API method name.

Used by fill_skipped and heal to understand:
- what HTTP method + path the test is calling
- what parameters are required and what values they accept
- what the response looks like (schema + whether it's Pydantic or raw dict)
"""
import re
from typing import Literal

import requests

ResponseType = Literal["pydantic", "list_pydantic", "dict", "list_dict", "none"]


def load_spec(swagger_url: str) -> dict:
    response = requests.get(swagger_url, timeout=15, verify=False)
    response.raise_for_status()
    return response.json()


def find_endpoint(spec: dict, api_method: str) -> dict | None:
    """
    Match a generated Python method name back to a spec path + HTTP method.

    Strategy:
    1. Try matching by operationId converted to snake_case (most reliable)
    2. Normalise every spec path to the method name openapi-generator would
       produce and compare — handles specs with no operationIds.
    """
    for path, path_item in spec.get("paths", {}).items():
        for http_method, operation in path_item.items():
            if http_method not in ("get", "post", "put", "patch", "delete"):
                continue

            # Strategy 1: match by operationId
            op_id = operation.get("operationId", "")
            if op_id and _to_snake(op_id) == api_method:
                return _build_endpoint_info(path, http_method, operation, spec)

            # Strategy 2: reconstruct method name from path + verb
            # openapi-generator: /v1/assets/{asset_type} + POST
            #   → strip braces, replace /- with _, append verb
            #   → v1_assets_asset_type_post
            if _path_to_method_name(path, http_method) == api_method:
                return _build_endpoint_info(path, http_method, operation, spec)

    return None


def _build_endpoint_info(
    path: str, http_method: str, operation: dict, spec: dict
) -> dict:
    info = {
        "path": path,
        "method": http_method.upper(),
        "summary": operation.get("summary", ""),
        "description": operation.get("description", ""),
        "parameters": _extract_parameters(operation, spec),
        "request_body": _extract_request_body(operation, spec),
        "response_schema": None,
        "response_type": "none",
    }

    schema, response_type = _extract_response(operation, spec)
    info["response_schema"] = schema
    info["response_type"] = response_type

    return info


def _extract_parameters(operation: dict, spec: dict) -> list[dict]:
    result = []
    for param in operation.get("parameters", []):
        param = _resolve_ref(param, spec)
        schema = _resolve_ref(param.get("schema", {}), spec)
        result.append({
            "name": param.get("name"),
            "in": param.get("in"),           # "path", "query", "header"
            "required": param.get("required", False),
            "description": param.get("description", ""),
            "type": schema.get("type"),
            "enum": schema.get("enum"),      # valid values if constrained
        })
    return result


def _extract_request_body(operation: dict, spec: dict) -> dict | None:
    request_body = operation.get("requestBody")
    if not request_body:
        return None
    content = request_body.get("content", {})
    for media_type in ("application/json", "application/x-www-form-urlencoded"):
        if media_type in content:
            schema = _resolve_ref(content[media_type].get("schema", {}), spec)
            return _flatten_schema(schema, spec)
    return None


def _extract_response(
    operation: dict, spec: dict
) -> tuple[dict | None, ResponseType]:
    """
    Find the 200/201 response schema and determine response_type:

    - pydantic       → response is a Pydantic model  (response.field)
    - list_pydantic  → list of Pydantic models        (response[0].field)
    - dict           → raw dict                       (response["field"])
    - list_dict      → list of raw dicts              (response[0]["field"])
    - none           → no response body
    """
    responses = operation.get("responses", {})
    for status in ("200", "201"):
        if status not in responses:
            continue
        resp = _resolve_ref(responses[status], spec)
        content = resp.get("content", {})
        for media_type in ("application/json", "text/plain", "text/json"):
            if media_type not in content:
                continue
            raw_schema = content[media_type].get("schema", {})
            schema = _resolve_ref(raw_schema, spec)
            response_type = _detect_response_type(raw_schema, schema, spec)
            return _flatten_schema(schema, spec), response_type

    return None, "none"


def _detect_response_type(
    raw_schema: dict, resolved_schema: dict, spec: dict
) -> ResponseType:
    """
    Determine whether the response will be a Pydantic model or raw dict.

    openapi-generator creates a Pydantic class when the schema has a $ref
    to a named component. Otherwise it falls back to dict.
    """
    # Direct $ref → named model → Pydantic
    if "$ref" in raw_schema:
        return "pydantic"

    schema_type = resolved_schema.get("type")

    if schema_type == "array":
        items = resolved_schema.get("items", {})
        # Array of $ref → List[PydanticModel]
        if "$ref" in items:
            return "list_pydantic"
        items_resolved = _resolve_ref(items, spec)
        # Array of objects with properties → List[PydanticModel] (inline schema)
        if items_resolved.get("properties"):
            return "list_pydantic"
        return "list_dict"

    if schema_type == "object" or resolved_schema.get("properties"):
        return "pydantic" if resolved_schema.get("properties") else "dict"

    return "dict"


# ── Schema helpers ────────────────────────────────────────────────────────────

def _flatten_schema(schema: dict, spec: dict, depth: int = 0) -> dict:
    """Recursively resolve $refs and summarise schema for prompt context."""
    if depth > 3:
        return {"type": "..."}
    schema = _resolve_ref(schema, spec)
    if not schema:
        return {}

    result = {}
    for key in ("type", "enum", "description", "required"):
        if key in schema:
            result[key] = schema[key]

    if "properties" in schema:
        result["properties"] = {
            k: _flatten_schema(v, spec, depth + 1)
            for k, v in schema["properties"].items()
        }

    if schema.get("type") == "array" and "items" in schema:
        result["items"] = _flatten_schema(schema["items"], spec, depth + 1)

    return result


def _resolve_ref(obj: dict, spec: dict) -> dict:
    """Follow a $ref pointer to its definition in the spec."""
    if not isinstance(obj, dict):
        return obj
    ref = obj.get("$ref")
    if not ref:
        return obj
    parts = ref.lstrip("#/").split("/")
    result = spec
    for part in parts:
        result = result.get(part, {})
    return result


# ── Name conversion helpers ───────────────────────────────────────────────────

def _to_snake(name: str) -> str:
    """Convert operationId (CamelCase) to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def _path_to_method_name(path: str, http_method: str) -> str:
    """
    Reconstruct the Python method name openapi-generator would produce.
    e.g. POST /v1/assets/{asset_type} → "v1_assets_asset_type_post"
    """
    # Remove braces around path params: {asset_type} → asset_type
    normalized = re.sub(r"\{([^}]+)\}", r"\1", path.lstrip("/"))
    # Replace slashes, hyphens with underscores
    normalized = re.sub(r"[/\-]", "_", normalized)
    # Collapse multiple underscores
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"{normalized}_{http_method.lower()}"
