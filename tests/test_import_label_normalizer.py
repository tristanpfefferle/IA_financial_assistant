from __future__ import annotations

from agent.import_label_normalizer import extract_observed_alias_from_label


def test_extract_observed_alias_from_label_coop() -> None:
    label = "Coop-2335 Villeneu;1844 ..."
    assert extract_observed_alias_from_label(label) == "Coop"


def test_extract_observed_alias_from_label_coop_pronto() -> None:
    label = "Coop Pronto 3488;1870 ..."
    assert extract_observed_alias_from_label(label) == "Coop Pronto"


def test_extract_observed_alias_from_label_sbb_mobile_with_twint_suffix() -> None:
    label = "SBB MOBILE; Paiement UBS TWINT Motif du paiement: ..."
    assert extract_observed_alias_from_label(label) == "SBB Mobile"


def test_extract_observed_alias_from_label_removes_no_transaction_suffix() -> None:
    label = "Solde décompte des prix prestations No de transaction: CM..."
    assert extract_observed_alias_from_label(label) == "Solde décompte des prix prestations"


def test_extract_observed_alias_from_label_masks_card_transfer_person_name() -> None:
    label = "XXXX XXXX XXXX 7708;TRISTAN PFEFFERLE Paiement à une carte Account no. IBAN: ..."
    assert extract_observed_alias_from_label(label) == "Paiement à une carte"


def test_extract_observed_alias_from_label_empty_or_none() -> None:
    assert extract_observed_alias_from_label("") is None
    assert extract_observed_alias_from_label(None) is None
