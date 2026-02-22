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
    observed_alias = extract_observed_alias_from_label(label)
    assert observed_alias is not None
    assert "paiement à une carte" in observed_alias.lower()


def test_extract_observed_alias_from_label_keeps_sumup_after_semicolon() -> None:
    label = "Paiement à une carte; SumUp *ABC SHOP 8002 ZURICH"
    observed_alias = extract_observed_alias_from_label(label)
    assert observed_alias is not None
    assert "sumup" in observed_alias.lower()
    assert "abc shop" in observed_alias.lower()


def test_extract_observed_alias_from_label_keeps_twint_signal() -> None:
    label = "Crédit UBS TWINT Motif du paiement: REMBOURSEMENT"
    observed_alias = extract_observed_alias_from_label(label)
    assert observed_alias is not None
    assert "twint" in observed_alias.lower()


def test_extract_observed_alias_from_label_keeps_twint_with_p2p_name_and_masks_long_digits() -> None:
    label = "Débit UBS TWINT Martin Dupont 79927398713"
    observed_alias = extract_observed_alias_from_label(label)
    assert observed_alias is not None
    assert "twint" in observed_alias.lower()
    assert "martin dupont" in observed_alias.lower()
    assert "79927398713" not in observed_alias
    assert "[NUM]" in observed_alias


def test_extract_observed_alias_from_label_redacts_iban_and_reference() -> None:
    label = "Paiement facture CH9300762011623852957 Reference no. RF18539007547034 123456789"
    observed_alias = extract_observed_alias_from_label(label)
    assert observed_alias is not None
    assert "[IBAN]" in observed_alias
    assert "[REF]" in observed_alias
    assert "123456789" not in observed_alias


def test_extract_observed_alias_from_label_empty_or_none() -> None:
    assert extract_observed_alias_from_label("") is None
    assert extract_observed_alias_from_label(None) is None
