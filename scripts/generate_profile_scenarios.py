"""Generate deterministic onboarding profile fuzz scenarios."""

from __future__ import annotations

import json
from pathlib import Path


def _one_turn_scenario(
    scenario_id: str,
    initial_profile: dict[str, str],
    user: str,
    *,
    ask_contains: list[str] | None = None,
    no_reset: bool = False,
    update_equals: dict[str, str] | None = None,
    no_llm: bool = False,
) -> dict[str, object]:
    expect: dict[str, object] = {}
    if ask_contains:
        expect["ask_contains"] = ask_contains
    if no_reset:
        expect["no_reset"] = True
    if update_equals:
        expect["update_equals"] = update_equals
    if no_llm:
        expect["no_llm"] = True
    return {"id": scenario_id, "initial_profile": initial_profile, "turns": [{"user": user, "expect": expect}]}


def _build_scenarios() -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []

    first_name_typos = [
        ("Mon pernom c'est jake", "Jake"),
        ("Mon pérnom c'est JAKE", "Jake"),
        ("prenon: tristan", "Tristan"),
        ("prénom: pfefferlé", "Pfefferlé"),
        ("Je m'appel tristan", "Tristan"),
        ("je m'appelle TRISTAN", "Tristan"),
        ("moi cest pAUl", "Paul"),
        ("moi c'est o'connor", "O'Connor"),
        ("moi cest jean-paul", "Jean-Paul"),
        ("c'est pfefferlé", "Pfefferlé"),
    ]
    for idx, (message, expected_first) in enumerate(first_name_typos, 1):
        scenarios.append(
            _one_turn_scenario(
                f"typo-first-{idx:03d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["nom de famille"],
                update_equals={"first_name": expected_first},
            )
        )

    refusal_messages = ["je sais pas", "j'en ai pas", "non", "nop", "nan", "j'en sais rien", "aucune idee", "j'ai pas"]
    for idx, refusal in enumerate(refusal_messages, 1):
        scenarios.append(
            _one_turn_scenario(
                f"refusal-last-{idx:03d}",
                {"first_name": "Paul", "last_name": "", "birth_date": ""},
                refusal,
                ask_contains=["nom de famille"],
                no_reset=True,
            )
        )

    meta_messages = [
        "je t'ai déjà dit",
        "tu connais",
        "je viens de te le dire",
        "t'es sérieux",
        "blague",
        "hein ?",
        "quoi ?",
        "sérieux ?",
    ]
    for idx, message in enumerate(meta_messages, 1):
        scenarios.append(
            _one_turn_scenario(
                f"meta-last-{idx:03d}",
                {"first_name": "Jake", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["nom de famille"],
                no_reset=True,
            )
        )

    low_signal = ["??", "...", "ok", "hein", "🙂", "🤷", "👍", "😶", "🙃", "🤔"]
    for idx, message in enumerate(low_signal, 1):
        scenarios.append(
            _one_turn_scenario(
                f"low-signal-{idx:03d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["prénom"],
            )
        )

    toxic_messages = ["ta gueule", "ftg", "tg", "connard", "pute", "ta gueule stp", "ftg sérieux", "espèce de connard"]
    for idx, message in enumerate(toxic_messages, 1):
        scenarios.append(
            _one_turn_scenario(
                f"toxic-{idx:03d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["prénom"],
                no_llm=True,
            )
        )

    mixed_inputs = [
        ("Jean Dupont 1992-05-10", {"first_name": "Jean", "last_name": "Dupont", "birth_date": "1992-05-10"}),
        ("je m'appelle marie durand 14/02/1998", {"first_name": "Marie", "last_name": "Durand", "birth_date": "1998-02-14"}),
        ("pierre martin le 10.11.2001", {"first_name": "Pierre", "last_name": "Martin", "birth_date": "2001-11-10"}),
        ("o'connor fitzgerald 2000-01-30", {"first_name": "O'Connor", "last_name": "Fitzgerald", "birth_date": "2000-01-30"}),
        ("jean-paul sartre 12 mai 1994", {"first_name": "Jean-Paul", "last_name": "Sartre", "birth_date": "1994-05-12"}),
    ]
    for idx, (message, expected_update) in enumerate(mixed_inputs, 1):
        scenarios.append(
            _one_turn_scenario(
                f"mixed-full-{idx:03d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["récapitulatif", "tout est correct"],
                update_equals=expected_update,
            )
        )

    birth_date_edges = [
        ("2002-01-14", "2002-01-14"),
        ("14/01/2002", "2002-01-14"),
        ("14.01.2002", "2002-01-14"),
        ("14 janvier 2002", "2002-01-14"),
        ("29/02/2000", "2000-02-29"),
        ("31/01/1999", "1999-01-31"),
        ("01/12/1988", "1988-12-01"),
        ("30.06.1997", "1997-06-30"),
        ("7 mars 2004", "2004-03-07"),
        ("2003-11-09", "2003-11-09"),
    ]
    for idx, (message, expected_date) in enumerate(birth_date_edges, 1):
        scenarios.append(
            _one_turn_scenario(
                f"birth-edge-{idx:03d}",
                {"first_name": "Tristan", "last_name": "Jadre", "birth_date": ""},
                message,
                ask_contains=["récapitulatif", "tout est correct"],
                update_equals={"birth_date": expected_date},
            )
        )

    year_typo_sequences = [
        ("Je suis né le 10 mai 20002", "2002-05-10"),
        ("14 janvier 20008", "2008-01-14"),
        ("29 fevrier 20004", "2004-02-29"),
        ("1 decembre 20008", "2008-12-01"),
        ("15 avril 20004", "2004-04-15"),
    ]
    for idx, (message, expected_date) in enumerate(year_typo_sequences, 1):
        scenarios.append(
            {
                "id": f"year-typo-confirm-{idx:03d}",
                "initial_profile": {"first_name": "Jake", "last_name": "Avassdd", "birth_date": ""},
                "turns": [
                    {
                        "user": message,
                        "expect": {
                            "ask_contains": ["confirmer ton année de naissance"],
                        },
                    },
                    {
                        "user": "oui",
                        "expect": {
                            "ask_contains": ["récapitulatif", "tout est correct"],
                            "update_equals": {"birth_date": expected_date},
                        },
                    },
                ],
            }
        )

    last_names = ["Milsap", "o'connor", "JEAN-PAUL", "duPont", "mc'arthy", "delaunay", "PFEFFERLÉ", "la-fontaine"]
    for idx, name in enumerate(last_names, 1):
        scenarios.append(
            _one_turn_scenario(
                f"last-name-only-{idx:03d}",
                {"first_name": "Paul", "last_name": "", "birth_date": ""},
                name,
                ask_contains=["date de naissance"],
                no_reset=True,
                update_equals={"last_name": name},
            )
        )

    generic_first_names = [
        "alain", "bernadette", "celine", "damien", "elise", "fabien", "gaelle", "hugo", "ines", "julien", "karim",
        "lea", "mathis", "nadia", "olivier", "pauline", "quentin", "romain", "sarah", "thomas", "ulysse", "victor",
    ]
    for idx, first in enumerate(generic_first_names, 1):
        scenarios.append(
            _one_turn_scenario(
                f"fallback-generic-{idx:03d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                f"moi cest {first}",
                ask_contains=["nom de famille"],
                update_equals={"first_name": first},
            )
        )

    return scenarios


def main() -> None:
    scenarios = _build_scenarios()
    out = Path("tests/fixtures/profile_scenarios.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for scenario in scenarios:
            handle.write(json.dumps(scenario, ensure_ascii=False) + "\n")
    print(f"wrote {len(scenarios)} scenarios to {out}")


if __name__ == "__main__":
    main()
