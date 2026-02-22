from __future__ import annotations

from backend.services.releves_import.parsers.ubs import parse_ubs_csv


def test_parse_ubs_csv_builds_label_from_additional_text_columns() -> None:
    content = """Numéro de compte: CH00 0000 0000 0000 0000 0
IBAN: CH00 0000 0000 0000 0000 0
Du: 01.01.2025
Au: 31.01.2025
Date de transaction;Date de comptabilisation;Description1;Description2;Description3;Motif du paiement;Information complémentaire;No de transaction;Débit;Crédit;Monnaie
10.01.2025;10.01.2025;Paiement UBS TWINT;;;Motif du paiement: SumUp *ABC SHOP;TWINT P2P Martin Dupont;TRX-001;12,50;;CHF
""".encode("utf-8")

    rows = parse_ubs_csv(content)

    assert len(rows) == 1
    libelle = rows[0]["libelle"]
    assert isinstance(libelle, str)
    assert "Paiement UBS TWINT" in libelle
    assert "SumUp" in libelle
    assert "TWINT P2P Martin Dupont" in libelle
    assert "TRX-001" not in libelle
