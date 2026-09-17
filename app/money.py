from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def format_money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def parse_money(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount") from exc
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents > 100_000_000:
        raise ValueError("Amount is too large")
    return cents
