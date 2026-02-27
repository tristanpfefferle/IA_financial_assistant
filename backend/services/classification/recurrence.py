"""Deterministic monthly recurrence detection for imported transactions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_VARIABLE_TOKENS = frozenset({"ref", "referenz", "avis", "message", "tx", "transaction"})
_IBAN_LIKE_REGEX = re.compile(r"\b[a-z]{2}\d{2}[a-z0-9]{8,30}\b")
_LONG_NUMBER_REGEX = re.compile(r"\b\d{4,}\b")
_STRICT_NUMERIC_TOKEN_REGEX = re.compile(r"^\d+$")
_LONG_ALNUM_REFERENCE_REGEX = re.compile(r"^(?=.*\d)[a-z0-9]{6,}$")
_NON_ALNUM_REGEX = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class RecurringCluster:
    """A stable cluster of monthly-like recurring transactions."""

    cluster_key: str
    sign: str
    amount_chf: int
    label_key: str
    transaction_ids: list[str]
    stats: dict[str, Any]


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


def normalize_label_key(payee: str | None, libelle: str | None) -> str:
    """Normalize a transaction label to a compact deterministic key."""

    text = _normalize_text(f"{payee or ''} {libelle or ''}")
    text = _IBAN_LIKE_REGEX.sub(" ", text)
    text = _LONG_NUMBER_REGEX.sub(" ", text)
    text = _NON_ALNUM_REGEX.sub(" ", text)

    tokens: list[str] = []
    for token in text.split():
        if token in _VARIABLE_TOKENS:
            continue
        if _STRICT_NUMERIC_TOKEN_REGEX.fullmatch(token):
            continue
        if _LONG_ALNUM_REFERENCE_REGEX.fullmatch(token):
            continue
        tokens.append(token)

    normalized = " ".join(tokens).strip()
    if len(normalized) > 80:
        return normalized[:80].rstrip()
    return normalized


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise ValueError("empty date")
        if "T" in candidate or " " in candidate:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        return date.fromisoformat(candidate)
    raise ValueError("unsupported date type")


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def detect_monthly_recurring_clusters(transactions: list[dict[str, Any]]) -> list[RecurringCluster]:
    """Detect monthly-like recurring clusters from raw transaction dictionaries."""

    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}

    for tx in transactions:
        tx_date = _parse_date(tx.get("date"))
        amount = _to_decimal(tx.get("montant"))
        if amount == 0:
            continue
        sign = "income" if amount > 0 else "expense"
        amount_chf = int(abs(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        label_key = normalize_label_key(tx.get("payee"), tx.get("libelle"))

        grouping_key = (sign, amount_chf, label_key)
        grouped.setdefault(grouping_key, []).append(
            {
                "id": str(tx.get("id")),
                "date": tx_date,
                "amount_abs": abs(amount),
                "libelle": str(tx.get("libelle") or "").strip(),
            }
        )

    clusters: list[RecurringCluster] = []

    for (sign, amount_chf, label_key), rows in grouped.items():
        if len(rows) < 4:
            continue

        ordered_rows = sorted(rows, key=lambda row: row["date"])
        deltas = [
            (ordered_rows[idx]["date"] - ordered_rows[idx - 1]["date"]).days
            for idx in range(1, len(ordered_rows))
        ]
        monthly_like_hits = sum(25 <= delta <= 35 for delta in deltas)
        if monthly_like_hits < 2:
            continue

        cluster_key = hashlib.sha1(f"{sign}|{amount_chf}|{label_key}".encode("utf-8")).hexdigest()
        sample_labels: list[str] = []
        for row in ordered_rows:
            label = row["libelle"]
            if not label:
                continue
            if label in sample_labels:
                continue
            sample_labels.append(label)
            if len(sample_labels) == 5:
                break

        total_amount_abs = sum((row["amount_abs"] for row in ordered_rows), Decimal("0"))

        clusters.append(
            RecurringCluster(
                cluster_key=cluster_key,
                sign=sign,
                amount_chf=amount_chf,
                label_key=label_key,
                transaction_ids=[row["id"] for row in ordered_rows],
                stats={
                    "count": len(ordered_rows),
                    "total_amount_abs": str(total_amount_abs),
                    "first_date": ordered_rows[0]["date"].isoformat(),
                    "last_date": ordered_rows[-1]["date"].isoformat(),
                    "sample_labels": sample_labels,
                },
            )
        )

    return sorted(clusters, key=lambda cluster: (cluster.sign, cluster.amount_chf, cluster.label_key))
