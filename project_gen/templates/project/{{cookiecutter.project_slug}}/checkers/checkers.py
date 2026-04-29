import json

import allure
from httpx import HTTPStatusError, codes
from contextlib import contextmanager


@contextmanager
@allure.step("check response status code")
def check_status_code_http(
        exception: type[Exception],
        expected_status_code: codes,
        expected_message: str = "",
        ):
    try:
        yield
        if expected_status_code != codes.OK:
            raise AssertionError(
                f"Expected status_code should be equal {expected_status_code}"
            )
        if expected_message:
            raise AssertionError(
                f"expected to get error message '{expected_message}' but got response without error"
            )
    except exception as e:
        assert e.status == expected_status_code
        assert json.loads(e.body)["title"] == expected_message