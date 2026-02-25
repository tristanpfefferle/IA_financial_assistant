from __future__ import annotations

from agent.loops.confidence import ConfidenceLevel, parse_profile_collect_message


def test_parse_single_name_high_confidence() -> None:
    parsed = parse_profile_collect_message("Paul")

    assert parsed["first_name"].value == "Paul"
    assert parsed["first_name"].confidence == ConfidenceLevel.HIGH
    assert "single_token_name" in parsed["first_name"].reasons


def test_parse_two_tokens_is_ambiguous_medium() -> None:
    parsed = parse_profile_collect_message("Paul Murt")

    assert parsed["first_name"].value == "Paul"
    assert parsed["last_name"].value == "Murt"
    assert parsed["first_name"].confidence == ConfidenceLevel.MEDIUM
    assert parsed["last_name"].confidence == ConfidenceLevel.MEDIUM
    assert "ambiguous_multi_token" in parsed["first_name"].reasons


def test_parse_two_names_with_date_extracts_birth_date_high() -> None:
    parsed = parse_profile_collect_message("Paul Murt 1990-01-01")

    assert parsed["birth_date"].value == "1990-01-01"
    assert parsed["birth_date"].confidence == ConfidenceLevel.HIGH
    assert parsed["first_name"].confidence in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
    assert parsed["last_name"].confidence in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}


def test_parse_long_message_with_conditions_low_confidence() -> None:
    message = (
        "Je m'appelle Paul mais uniquement si ça reste privé, et au fait je préfère "
        "répondre plus tard parce que ce formulaire est long et je veux comprendre "
        "chaque détail avant de continuer."
    )
    parsed = parse_profile_collect_message(message)

    assert parsed["first_name"].confidence == ConfidenceLevel.LOW
    assert "contains_conditions" in parsed["first_name"].reasons
