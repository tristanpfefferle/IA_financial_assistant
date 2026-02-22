"""Deterministic transaction classification for CSV imports.

Rules priority (strict):
1) internal transfers
2) TWINT person-to-person pending
3) salary income
4) other income
5) banking fees
6) taxes / insurance / subscriptions
7) merchant fallback then generic fallback
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

TxKind = Literal["expense", "income", "transfer_internal"]
CategoryStatus = Literal["confirmed", "pending"]


@dataclass(frozen=True, slots=True)
class ClassifiedTransaction:
    """Normalized classification result attached to imported transactions."""

    tx_kind: TxKind
    category_key: str
    category_label: str
    category_status: CategoryStatus


_SYSTEM_CATEGORY_LABELS: dict[str, str] = {
    "income_salary": "Salaire",
    "income_other": "Autres revenus",
    "transfer_internal": "Transferts internes",
    "twint_p2p_pending": "À catégoriser (TWINT)",
    "housing": "Logement",
    "food": "Alimentation",
    "transport": "Transport",
    "health": "Santé",
    "leisure": "Loisirs",
    "shopping": "Shopping",
    "subscriptions": "Abonnements",
    "insurance": "Assurance",
    "taxes": "Impôts",
    "banking_fees": "Frais bancaires",
    "gifts": "Cadeaux & dons",
    "savings": "Épargne & investissement",
    "other": "Autres",
}

_LEGACY_CATEGORY_KEY_ALIASES: dict[str, str] = {
    "bills": "subscriptions",
}

_MERCHANT_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("migros", "coop", "lidl", "aldi", "denner"), "food"),
    (("sbb", "cff", "tpg", "tl", "uber", "bolt"), "transport"),
    (("spotify", "netflix", "youtube", "apple", "google"), "subscriptions"),
)

_KNOWN_BANK_MARKERS = (
    "ubs",
    "raiffeisen",
    "revolut",
    "postfinance",
    "credit suisse",
    "bcv",
)

_KNOWN_MERCHANT_MARKERS = (
    "migros",
    "coop",
    "lidl",
    "aldi",
    "denner",
    "galaxus",
    "digitec",
    "ikea",
    "manor",
    "mcdonald",
    "starbucks",
)

_TWINT_P2P_MARKERS_REGEX = re.compile(r"\b(envoi|transfert|p2p|peer)\b")
_TWINT_P2P_NAME_REGEX = re.compile(r"\btwint\b.*\ba\b\s+([a-z]{2,})\s+([a-z]{2,})")
_TWINT_NON_PERSON_TOKENS = frozenset({"la", "le", "les", "un", "une", "des", "du", "de", "d", "au", "aux"})
_TAXES_REGEX = re.compile(r"\b(tax|impot|impots|steuer|estv|afc|vat)\b")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


def _joined_text(row: dict[str, Any]) -> str:
    return _normalize_text(f"{row.get('payee') or ''} {row.get('libelle') or ''}")


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _is_internal_transfer(text: str) -> bool:
    direct_markers = (
        "virement interne",
        "transfert interne",
        "entre comptes",
        "internal transfer",
        "transfer between accounts",
    )
    if any(marker in text for marker in direct_markers):
        return True

    bank_hits = sum(1 for marker in _KNOWN_BANK_MARKERS if marker in text)
    if bank_hits >= 2:
        return True

    topup_markers = ("top up", "topup", "recharge")
    return any(marker in text for marker in topup_markers) and any(bank in text for bank in _KNOWN_BANK_MARKERS)


def _is_twint_p2p(text: str) -> bool:
    if "twint" not in text:
        return False

    if _pick_fallback_category_key(text) != "other":
        return False

    if any(merchant in text for merchant in _KNOWN_MERCHANT_MARKERS):
        return False

    if _TWINT_P2P_MARKERS_REGEX.search(text):
        return True

    name_match = _TWINT_P2P_NAME_REGEX.search(text)
    if not name_match:
        return False

    first_name_like, last_name_like = name_match.group(1), name_match.group(2)
    return first_name_like not in _TWINT_NON_PERSON_TOKENS and last_name_like not in _TWINT_NON_PERSON_TOKENS


def _pick_fallback_category_key(text: str) -> str:
    for keywords, category_key in _MERCHANT_CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category_key
    return "other"


def classify_and_categorize_transaction(row: dict[str, Any]) -> ClassifiedTransaction:
    """Classify one imported transaction with deterministic priority rules.

    This helper is designed for CSV import pipelines. It preempts merchant fallback
    for internal transfers and TWINT P2P transactions and returns a category status
    so pending categories can be surfaced to users later.
    """

    text = _joined_text(row)
    amount = _to_decimal(row.get("montant"))

    if _is_internal_transfer(text):
        return ClassifiedTransaction("transfer_internal", "transfer_internal", _SYSTEM_CATEGORY_LABELS["transfer_internal"], "confirmed")

    if _is_twint_p2p(text):
        tx_kind: TxKind = "income" if amount > 0 else "expense"
        return ClassifiedTransaction(tx_kind, "twint_p2p_pending", _SYSTEM_CATEGORY_LABELS["twint_p2p_pending"], "pending")

    if amount > 0:
        salary_markers = ("salaire", "salary", "payroll", "lohn", "salar")
        refund_markers = ("refund", "remboursement", "retour", "chargeback", "storno")
        if any(marker in text for marker in salary_markers) and not any(marker in text for marker in refund_markers):
            return ClassifiedTransaction("income", "income_salary", _SYSTEM_CATEGORY_LABELS["income_salary"], "confirmed")
        return ClassifiedTransaction("income", "income_other", _SYSTEM_CATEGORY_LABELS["income_other"], "confirmed")

    banking_fee_markers = ("frais", "fee", "commission", "cotisation", "maintenance")
    if any(marker in text for marker in banking_fee_markers) and any(bank in text for bank in _KNOWN_BANK_MARKERS):
        return ClassifiedTransaction("expense", "banking_fees", _SYSTEM_CATEGORY_LABELS["banking_fees"], "confirmed")

    if _TAXES_REGEX.search(text):
        return ClassifiedTransaction("expense", "taxes", _SYSTEM_CATEGORY_LABELS["taxes"], "confirmed")

    insurance_markers = ("assurance", "insurance", "axa", "zurich", "helvetia", "mobiliar")
    if any(marker in text for marker in insurance_markers):
        return ClassifiedTransaction("expense", "insurance", _SYSTEM_CATEGORY_LABELS["insurance"], "confirmed")

    subscriptions_markers = ("subscription", "abonnement", "mensuel", "monthly", "spotify", "netflix", "apple", "google")
    if any(marker in text for marker in subscriptions_markers):
        return ClassifiedTransaction("expense", "subscriptions", _SYSTEM_CATEGORY_LABELS["subscriptions"], "confirmed")

    fallback_key = _pick_fallback_category_key(text)
    if amount < 0:
        return ClassifiedTransaction("expense", fallback_key, _SYSTEM_CATEGORY_LABELS.get(fallback_key, _SYSTEM_CATEGORY_LABELS["other"]), "confirmed")
    return ClassifiedTransaction("income", "income_other", _SYSTEM_CATEGORY_LABELS["income_other"], "confirmed")


def category_key_to_label(category_key: str) -> str:
    normalized_key = _LEGACY_CATEGORY_KEY_ALIASES.get(category_key, category_key)
    return _SYSTEM_CATEGORY_LABELS.get(normalized_key, _SYSTEM_CATEGORY_LABELS["other"])
