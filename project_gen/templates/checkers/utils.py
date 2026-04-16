"""
Shared response checker utility.

check_response() reads the non_null_fields.json snapshot saved by fill_skipped
and dynamically asserts that all previously non-null fields are still non-null.

This way checker files stay tiny and work for any model — no hardcoded field names.
"""
import json
from pathlib import Path

from assertpy import assert_that, soft_assertions


def check_response(response, snapshot_dir: Path) -> None:
    """
    Assert that the response structure matches the saved snapshot.

    - response not None
    - for list responses: at least 0 items
    - all fields that were non-null in the sample are still non-null
    """
    non_null_path = snapshot_dir / "non_null_fields.json"

    with soft_assertions():
        assert_that(response).is_not_none()

        is_list = isinstance(response, list)
        if is_list:
            assert_that(len(response)).is_greater_than_or_equal_to(0)

        if not non_null_path.exists():
            return

        non_null_fields: list[str] = json.loads(non_null_path.read_text())
        if not non_null_fields:
            return

        item = response[0] if is_list else response
        item_dict = item.model_dump() if hasattr(item, "model_dump") else item

        for field in non_null_fields:
            assert_that(item_dict.get(field)).described_as(field).is_not_none()
