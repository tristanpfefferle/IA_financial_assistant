from decimal import Decimal

from backend.services.releves_import.classification import classify_and_categorize_transaction


def _tx(*, montant: str, libelle: str, payee: str = "") -> dict[str, object]:
    return {"montant": Decimal(montant), "libelle": libelle, "payee": payee}


def test_salary_positive_keyword_is_income_salary_confirmed() -> None:
    result = classify_and_categorize_transaction(_tx(montant="3200.00", libelle="Salaire mensuel"))
    assert result.tx_kind == "income"
    assert result.category_key == "income_salary"
    assert result.category_status == "confirmed"


def test_refund_positive_not_salary_goes_to_income_other() -> None:
    result = classify_and_categorize_transaction(_tx(montant="80.00", libelle="Salary refund remboursement"))
    assert result.category_key == "income_other"


def test_internal_transfer_has_priority() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-500.00", libelle="Virement interne UBS Revolut"))
    assert result.tx_kind == "transfer_internal"
    assert result.category_key == "transfer_internal"
    assert result.category_status == "confirmed"


def test_twint_p2p_negative_is_pending() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-45.00", libelle="TWINT envoi à Martin Dupont"))
    assert result.category_key == "twint_p2p_pending"
    assert result.category_status == "pending"


def test_twint_merchant_is_not_pending_and_uses_merchant_fallback() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-25.00", libelle="TWINT MIGROS"))
    assert result.category_key == "food"
    assert result.category_status == "confirmed"


def test_banking_fees_category() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-8.00", libelle="UBS frais de tenue de compte"))
    assert result.category_key == "banking_fees"


def test_taxes_category() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-300.00", libelle="AFC impots cantonaux"))
    assert result.category_key == "taxes"


def test_insurance_category() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-120.00", libelle="Prime AXA assurance"))
    assert result.category_key == "insurance"


def test_subscriptions_category() -> None:
    result = classify_and_categorize_transaction(_tx(montant="-14.99", libelle="Spotify subscription"))
    assert result.category_key == "subscriptions"
