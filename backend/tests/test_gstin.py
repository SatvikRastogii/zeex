import pytest

from app.domain.gstin import is_valid_gstin


@pytest.mark.parametrize("ok", ["09AABCS1234K1Z5", "27AAPFU0939F1ZV", "07AAKFD1004D1Z9"])
def test_valid(ok: str) -> None:
    assert is_valid_gstin(ok)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "09AABCS1234K1Z",
        "09aabcs1234k1z5",
        "99AABCS1234K1Z5",
        "09AABCS1234K0Z5",
        "09AABCS1234K1X5",
    ],
)
def test_invalid(bad: str | None) -> None:
    assert not is_valid_gstin(bad)
