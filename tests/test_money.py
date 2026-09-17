import pytest

from app.money import format_money, parse_money


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 100), ("1.67", 167), ("$12.50", 1250), ("1,234.56", 123456)],
)
def test_parse_money(value: str, expected: int) -> None:
    assert parse_money(value) == expected


@pytest.mark.parametrize("value", ["", "free", "0", "-1"])
def test_parse_money_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_money(value)


def test_format_money() -> None:
    assert format_money(167) == "$1.67"
    assert format_money(123456) == "$1,234.56"
