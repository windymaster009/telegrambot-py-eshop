import pytest

from app.aba_payments import parse_aba_payment


def test_parses_current_english_aba_notification() -> None:
    payment = parse_aba_payment(
        "$100 paid by NHIM KEVIN (*606) on Sep 17, 10:46 PM via ABA PAY at "
        "MeeS by K.NHIM. Trx. ID: 178965995953299, APV: 886284."
    )

    assert payment is not None
    assert payment.transaction_id == "178965995953299"
    assert payment.amount_minor == 10_000
    assert payment.currency == "USD"
    assert payment.payer_name == "NHIM KEVIN"
    assert payment.payer_account == "*606"
    assert payment.channel == "ABA PAY"
    assert payment.merchant == "MeeS by K.NHIM"
    assert payment.apv == "886284"


def test_parses_older_transaction_number_wording() -> None:
    payment = parse_aba_payment(
        "$10.07 was paid by TEST CUSTOMER (*123) on Sep 17 via ABA PAY at BLINK. "
        "Transaction number: 123456789, APV: 222333."
    )

    assert payment is not None
    assert payment.amount_minor == 1007
    assert payment.transaction_id == "123456789"
    assert payment.payer_name == "TEST CUSTOMER"


def test_parses_khmer_aba_notification() -> None:
    payment = parse_aba_payment(
        "៛100,000 ត្រូវបានបង់ដោយ អតិថិជន សាកល្បង (*321) នៅថ្ងៃទី 17 កញ្ញា "
        "តាម ABA PAY នៅ BLINK។ លេខប្រតិបត្តិការ: 99887766, APV: 445566។"
    )

    assert payment is not None
    assert payment.amount_minor == 100_000
    assert payment.currency == "KHR"
    assert payment.transaction_id == "99887766"
    assert payment.payer_name == "អតិថិជន សាកល្បង"


@pytest.mark.parametrize(
    "text",
    [
        None,
        "hello",
        "$10 paid by TEST (*123)",
        "$0 paid by TEST (*123). Trx. ID: 123",
    ],
)
def test_rejects_non_payment_messages(text: str | None) -> None:
    assert parse_aba_payment(text) is None
