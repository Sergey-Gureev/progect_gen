"""Use Claude API to generate test body from endpoint spec + real response."""
import json

import anthropic


SYSTEM_PROMPT = """You are an expert Python test engineer.
Your job is to rewrite a skipped pytest test so it actually runs and asserts correctly.

Rules:
- Remove @pytest.mark.skip decorator
- Fill required parameters with realistic values based on the spec (use enum values if available)
- For optional params: use None unless a specific value improves test coverage
- Keep the existing response = await ... call unchanged
- Replace `assert response` with specific, meaningful assertions based on the response schema
- If a real response sample is provided, use it to write precise assertions
- Use assertpy: `from assertpy import assert_that` and `assert_that(response).is_not_none()`
- For list responses: assert length, assert first element has expected fields
- For object responses: assert key fields are not None and have expected types
- Return ONLY the complete Python file content, no explanations, no markdown fences
"""


def generate_healed_test(
    original_source: str,
    endpoint_info: dict,
    real_response: dict | list | None,
    client: anthropic.Anthropic,
) -> str:
    context_parts = [
        f"Endpoint: {endpoint_info['method']} {endpoint_info['path']}",
        f"Summary: {endpoint_info['summary']}",
    ]

    params = endpoint_info.get("parameters", [])
    if params:
        context_parts.append("\nParameters:")
        for p in params:
            req = "REQUIRED" if p["required"] else "optional"
            enum_hint = f", enum: {p['enum']}" if p.get("enum") else ""
            context_parts.append(f"  - {p['name']} ({p['in']}, {req}, type: {p['type']}{enum_hint}): {p['description']}")

    req_body = endpoint_info.get("request_body")
    if req_body:
        context_parts.append(f"\nRequest body schema:\n{json.dumps(req_body, indent=2)}")

    resp_schema = endpoint_info.get("response_schema")
    if resp_schema:
        context_parts.append(f"\nResponse schema:\n{json.dumps(resp_schema, indent=2)}")

    if real_response is not None:
        sample = real_response[:2] if isinstance(real_response, list) else real_response
        context_parts.append(f"\nReal API response sample:\n{json.dumps(sample, indent=2, default=str)}")

    user_message = f"""Rewrite this skipped test to make it pass:

```python
{original_source}
```

API context:
{chr(10).join(context_parts)}
"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    result = message.content[0].text.strip()

    # Strip markdown fences if Claude wrapped it anyway
    if result.startswith("```python"):
        result = result[9:]
    if result.startswith("```"):
        result = result[3:]
    if result.endswith("```"):
        result = result[:-3]

    return result.strip()
