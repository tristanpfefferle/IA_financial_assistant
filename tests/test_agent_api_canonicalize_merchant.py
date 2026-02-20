"""Unit tests for deterministic merchant canonicalization in agent API."""

from __future__ import annotations

from agent.api import _canonicalize_merchant


def test_canonicalize_merchant_ignores_les_prefix() -> None:
    canonical = _canonicalize_merchant("LES BAINS DE LAVEY S.A.; Paiement UBS...")

    assert canonical is not None
    name, name_norm, alias = canonical
    assert name in {"Bains", "Bains Lavey"}
    assert name_norm in {"bains", "bains lavey"}
    assert alias == "LES BAINS DE LAVEY S.A.; Paiement UBS..."


def test_canonicalize_merchant_skips_suspect_first_name() -> None:
    canonical = _canonicalize_merchant("Tristan Pfefferle; paiement UBS")

    assert canonical is not None
    name, name_norm, _ = canonical
    assert name != "Tristan"
    assert name_norm != "tristan"


def test_canonicalize_merchant_restaurant_prefers_distinctive_token() -> None:
    canonical = _canonicalize_merchant("Restaurant HUIT; Paiement carte")

    assert canonical is not None
    name, name_norm, _ = canonical
    assert name == "Huit"
    assert name_norm == "huit"


def test_canonicalize_merchant_drops_generic_head_without_known_brand_rule() -> None:
    canonical = _canonicalize_merchant("Restaurant COQUOZ; Paiement carte")

    assert canonical is not None
    name, name_norm, _ = canonical
    assert name == "Coquoz"
    assert name_norm == "coquoz"


def test_canonicalize_merchant_station_service_prefers_brand_name() -> None:
    canonical = _canonicalize_merchant("Station-service Migrol; Carte")

    assert canonical is not None
    name, name_norm, _ = canonical
    assert name.startswith("Migrol")
    assert name != "Station-service"
    assert name_norm.startswith("migrol")


def test_canonicalize_merchant_avoids_caisse_stopword() -> None:
    canonical = _canonicalize_merchant("Caisse de compensation AVS")

    assert canonical is not None
    name, name_norm, _ = canonical
    assert name != "Caisse"
    assert name_norm != "caisse"
    assert "compensation" in name_norm


def test_canonicalize_merchant_keeps_known_brand_coop() -> None:
    canonical = _canonicalize_merchant("COOP-4815 MONTHEY")

    assert canonical == ("Coop", "coop", "COOP-4815 MONTHEY")


def test_canonicalize_merchant_keeps_known_brand_migrol() -> None:
    canonical = _canonicalize_merchant("Station-service Migrol 1234")

    assert canonical == (
        "Migrol",
        "migrol",
        "Station-service Migrol 1234",
    )
