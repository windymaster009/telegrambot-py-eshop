from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class AbaPayment:
    transaction_id: str
    amount_minor: int
    currency: str
    payer_name: str
    payer_account: str
    apv: str
    channel: str
    merchant: str
    paid_at_text: str = ""


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_aba_payment(text: str | None) -> AbaPayment | None:
    """Parse English or Khmer PayWay by ABA Telegram notifications."""
    if not text:
        return None

    money_match = re.search(r"^\s*([$៛])\s*([\d,]+(?:\.\d{1,2})?)", text)
    if money_match is None:
        return None
    try:
        amount = Decimal(money_match.group(2).replace(",", ""))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None

    currency = "USD" if money_match.group(1) == "$" else "KHR"
    if currency == "USD":
        amount_minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        amount_minor = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    transaction_match = re.search(
        r"(?:Trx\.?\s*ID|Transaction(?:\s+number)?|លេខប្រតិបត្តិការ)\s*:\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    if transaction_match is None:
        return None

    english_payer = re.search(
        r"(?:was\s+)?paid\s+by\s+(.+?)\s+\(\*(\d+)\)", text, re.IGNORECASE
    )
    khmer_payer = re.search(r"ត្រូវបានបង់ដោយ\s+(.+?)\s+\(\*(\d+)\)", text)
    payer_match = english_payer or khmer_payer
    if payer_match is None:
        return None

    apv = re.search(r"APV\s*:\s*(\d+)", text, re.IGNORECASE)
    english_route = re.search(
        r"via\s+(.+?)\s+at\s+(.+?)(?:\.|។)\s*"
        r"(?:Trx\.?\s*ID|Transaction(?:\s+number)?)",
        text,
        re.IGNORECASE,
    )
    khmer_route = re.search(
        r"តាម\s+(.+?)\s+នៅ\s+(.+?)(?:\.|។)\s*"
        r"(?:លេខប្រតិបត្តិការ|Transaction(?:\s+number)?)",
        text,
        re.IGNORECASE,
    )
    route_match = english_route or khmer_route
    english_paid_at = re.search(r"\(\*\d+\)\s+on\s+(.+?)\s+via\s+", text, re.IGNORECASE)
    khmer_paid_at = re.search(r"\(\*\d+\)\s+(?:នៅថ្ងៃទី|នៅ)\s+(.+?)\s+តាម\s+", text)
    paid_at_match = english_paid_at or khmer_paid_at

    return AbaPayment(
        transaction_id=transaction_match.group(1),
        amount_minor=amount_minor,
        currency=currency,
        payer_name=_clean(payer_match.group(1)),
        payer_account=f"*{payer_match.group(2)}",
        apv=apv.group(1) if apv else "",
        channel=_clean(route_match.group(1) if route_match else "ABA PayWay"),
        merchant=_clean(route_match.group(2) if route_match else ""),
        paid_at_text=_clean(paid_at_match.group(1) if paid_at_match else ""),
    )
