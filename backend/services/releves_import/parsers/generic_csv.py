"""Generic CSV parser adapted from legacy lire_csv_contenu."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import Any


def _parse_date(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _parse_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", "").replace("'", "").replace("’", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def parse_generic_csv(file_bytes: bytes) -> list[dict[str, Any]]:
    content = file_bytes.decode("utf-8", errors="ignore")
    buffer = io.StringIO(content)
    try:
        dialect = csv.Sniffer().sniff(buffer.read(2048))
        buffer.seek(0)
    except csv.Error:
        buffer.seek(0)
        dialect = csv.excel

    reader = csv.DictReader(buffer, dialect=dialect)
    rows: list[dict[str, Any]] = []
    for raw in reader:
        lower = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}
        amount = _parse_amount(lower.get("montant") or lower.get("amount") or lower.get("credit/debit amount"))
        label = lower.get("libelle") or lower.get("description") or lower.get("text")
        payee = lower.get("payee") or lower.get("bénéficiaire") or lower.get("beneficiary")
        rows.append(
            {
                "date": _parse_date(lower.get("date") or lower.get("booking date") or lower.get("datum") or lower.get("booked at")),
                "libelle": str(label).strip() if label else None,
                "montant": amount,
                "devise": str(lower.get("devise") or lower.get("currency") or "CHF"),
                "categorie": lower.get("categorie"),
                "payee": str(payee).strip() if payee else None,
                "meta": raw,
            }
        )
    return rows
