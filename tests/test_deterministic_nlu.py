from datetime import date

import pytest

from agent.deterministic_nlu import parse_intent, parse_search_query_parts


@pytest.mark.parametrize(
    ("message", "expected_name"),
    [
        ("Crée un compte UBS", "UBS"),
        ("Créer un compte bancaire nommé UBS", "UBS"),
        ("Ajoute un compte 'UBS'", "UBS"),
        ("Nouveau compte: UBS", "UBS"),
        ("Crée un compte bancaire Revolut", "Revolut"),
        ("ajoute un compte Neon.", "Neon"),
    ],
)
def test_parse_account_create_variants(message: str, expected_name: str) -> None:
    intent = parse_intent(message)

    assert intent == {
        "type": "tool_call",
        "tool_name": "finance_bank_accounts_create",
        "payload": {"name": expected_name},
    }


@pytest.mark.parametrize(
    "message", ["Crée un compte", "Créer un compte bancaire", "Nouveau compte:"]
)
def test_parse_account_create_missing_name_needs_clarification(message: str) -> None:
    intent = parse_intent(message)

    assert intent == {
        "type": "clarification",
        "message": "Quel nom voulez-vous donner au compte bancaire ?",
    }


@pytest.mark.parametrize(
    "message",
    [
        "liste mes comptes",
        "quels sont mes comptes bancaires",
        "Quels sont mes comptes",
        "affiche mes comptes",
    ],
)
def test_parse_accounts_list_variants(message: str) -> None:
    assert parse_intent(message) == {
        "type": "tool_call",
        "tool_name": "finance_bank_accounts_list",
        "payload": {},
    }


def test_parse_accounts_list_does_not_match_phrase_in_middle() -> None:
    assert parse_intent("si possible affiche mes comptes et transactions") is None


@pytest.mark.parametrize(
    "message",
    [
        "je veux importer un relevé",
        "importer un csv",
        "ajouter un relevé UBS",
        "Peux-tu importer un relevé ?",
    ],
)
def test_parse_import_variants_as_ui_action(message: str) -> None:
    assert parse_intent(message) == {
        "type": "ui_action",
        "action": "open_import_panel",
    }


@pytest.mark.parametrize(
    ("message", "merchant"),
    [
        ("cherche coffee", "coffee"),
        ("recherche Migros", "migros"),
        ("montre moi les transactions Migros", "migros"),
        ("montre les transactions de Coop", "coop"),
    ],
)
def test_parse_search_variants(message: str, merchant: str) -> None:
    intent = parse_intent(message)

    assert intent is not None
    assert intent["type"] == "tool_call"
    assert intent["tool_name"] == "finance_releves_search"
    assert intent["payload"]["merchant"] == merchant
    assert intent["payload"]["limit"] == 50
    assert intent["payload"]["offset"] == 0


@pytest.mark.parametrize(
    ("message", "start_date", "end_date"),
    [
        ("recherche Migros en janvier 2025", date(2025, 1, 1), date(2025, 1, 31)),
        ("cherche coop en févr. 2024", date(2024, 2, 1), date(2024, 2, 29)),
        (
            "montre moi les transactions Migros en decembre 2023",
            date(2023, 12, 1),
            date(2023, 12, 31),
        ),
    ],
)
def test_parse_search_with_month_year(
    message: str, start_date: date, end_date: date
) -> None:
    intent = parse_intent(message)

    assert intent is not None
    payload = intent["payload"]
    assert payload["date_range"] == {"start_date": start_date, "end_date": end_date}


@pytest.mark.parametrize(
    ("message", "expected_date_range"),
    [
        ("cherche", None),
        (
            "recherche en janvier 2025",
            {"start_date": date(2025, 1, 1), "end_date": date(2025, 1, 31)},
        ),
        ("montre les transactions", None),
    ],
)
def test_parse_search_without_merchant_returns_clarification(
    message: str,
    expected_date_range: dict[str, date] | None,
) -> None:
    intent = parse_intent(message)

    assert intent is not None
    assert intent["type"] == "clarification"
    assert isinstance(intent["message"], str)
    assert intent["message"]
    assert intent["clarification_type"] == "awaiting_search_merchant"
    assert intent.get("date_range") == expected_date_range


def test_parse_search_sets_non_empty_merchant_for_tool_call() -> None:
    intent = parse_intent("cherche Migros")

    assert intent is not None
    assert intent["type"] == "tool_call"
    assert intent["tool_name"] == "finance_releves_search"
    assert intent["payload"]["merchant"]


@pytest.mark.parametrize(
    "message",
    [
        "bonjour",
        "merci",
        "fais une blague",
    ],
)
def test_parse_intent_returns_none_for_unsupported_messages(message: str) -> None:
    assert parse_intent(message) is None


def test_parse_search_query_parts_extracts_known_bank_hint() -> None:
    parts = parse_search_query_parts("cherche Migros UBS")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "ubs",
        "date_range": None,
        "merchant_fallback": "migros ubs",
    }


def test_parse_search_query_parts_keeps_unknown_suffix_in_merchant() -> None:
    parts = parse_search_query_parts("cherche Migros UnknownBank")

    assert parts == {
        "merchant_text": "migros unknownbank",
        "bank_account_hint": None,
        "date_range": None,
    }


def test_parse_search_query_parts_handles_punctuation_on_bank_hint() -> None:
    parts = parse_search_query_parts("cherche Migros UBS!!!")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "ubs",
        "date_range": None,
        "merchant_fallback": "migros ubs",
    }


def test_parse_search_query_parts_extracts_multi_word_bank_hint() -> None:
    parts = parse_search_query_parts("cherche Migros credit suisse")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "credit suisse",
        "date_range": None,
        "merchant_fallback": "migros credit suisse",
    }


def test_parse_search_query_parts_extracts_accented_multi_word_bank_hint() -> None:
    parts = parse_search_query_parts("cherche Migros crédit suisse")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "crédit suisse",
        "date_range": None,
        "merchant_fallback": "migros crédit suisse",
    }


def test_parse_search_query_parts_extracts_bank_hint_before_suffix() -> None:
    parts = parse_search_query_parts("cherche Migros revolut pro")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "revolut",
        "date_range": None,
        "merchant_fallback": "migros revolut pro",
    }


def test_parse_search_query_parts_extracts_multi_word_bank_hint_with_punctuation() -> (
    None
):
    parts = parse_search_query_parts("cherche Migros credit suisse!!!")

    assert parts == {
        "merchant_text": "migros",
        "bank_account_hint": "credit suisse",
        "date_range": None,
        "merchant_fallback": "migros credit suisse",
    }


def test_parse_search_query_parts_sets_merchant_fallback_for_credit_suisse() -> None:
    parts = parse_search_query_parts("cherche Migros crédit suisse")

    assert parts["merchant_fallback"] == "migros crédit suisse"


def test_parse_search_query_parts_sets_merchant_fallback_for_revolut_pro() -> None:
    parts = parse_search_query_parts("cherche Migros revolut pro")

    assert parts["merchant_fallback"] == "migros revolut pro"
