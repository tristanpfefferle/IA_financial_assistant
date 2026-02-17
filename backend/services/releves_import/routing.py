"""Bank parser routing adapted from legacy routeur_comptes."""

from __future__ import annotations

from backend.services.releves_import.parsers.generic_csv import parse_generic_csv
from backend.services.releves_import.parsers.interactive_brokers import parse_interactive_brokers_csv
from backend.services.releves_import.parsers.pdf_ubs import parse_ubs_pdf
from backend.services.releves_import.parsers.raiffeisen import parse_raiffeisen_csv
from backend.services.releves_import.parsers.ubs import parse_ubs_csv
from backend.services.releves_import.source_detection import detect_source


def route_bank_parser(filename: str, content: bytes) -> tuple[str, list[dict[str, object]]]:
    source = detect_source(filename, content)
    name = filename.lower()

    if source == "interactivebrokers":
        return source, parse_interactive_brokers_csv(content)

    if source == "ubs":
        if name.endswith(".csv"):
            return source, parse_ubs_csv(content)
        if name.endswith(".pdf"):
            return source, parse_ubs_pdf(content)
        raise ValueError("Format de fichier non pris en charge pour UBS")

    if source == "raiffeisen":
        if name.endswith(".csv"):
            return source, parse_raiffeisen_csv(content)
        raise ValueError("Format de fichier non pris en charge pour Raiffeisen")

    if source in {"swissquote", "revolut", "degiro"}:
        if name.endswith(".csv"):
            return source, parse_generic_csv(content)
        raise ValueError("Format de fichier non pris en charge pour banque")

    if name.endswith(".csv"):
        return "generic", parse_generic_csv(content)

    raise ValueError(f"Source non supportée: {source}")
