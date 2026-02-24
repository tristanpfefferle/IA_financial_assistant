"""Generate deterministic onboarding profile fuzz scenarios."""

from __future__ import annotations

import argparse
import json
import random
from datetime import date
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
    return {
        "id": scenario_id,
        "initial_profile": initial_profile,
        "turns": [{"user": user, "expect": expect}],
    }


def _title_case_name(raw: str) -> str:
    chunks = [part.capitalize() for part in raw.split("-")]
    return "-".join(chunks)


def _iso_from_parts(day: int, month: int, year: int) -> str:
    return date(year, month, day).isoformat()


def _compute_distribution(total: int) -> dict[str, int]:
    weights = {
        "meta": 0.25,
        "refusal": 0.20,
        "typo": 0.15,
        "mixed": 0.15,
        "low_signal": 0.10,
        "toxic": 0.10,
        "birth_weird": 0.05,
    }
    counts = {key: int(total * weight) for key, weight in weights.items()}
    remaining = total - sum(counts.values())
    ordered = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    idx = 0
    while remaining > 0:
        counts[ordered[idx % len(ordered)][0]] += 1
        idx += 1
        remaining -= 1
    return counts


def _build_scenarios(count: int, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    scenarios: list[dict[str, object]] = []
    distribution = _compute_distribution(count)

    first_names = [
        "jean", "marie", "thomas", "emma", "lucas", "lea", "nicolas", "camille", "antoine", "julie",
        "hugo", "sarah", "mathis", "ines", "paul", "clara", "adrien", "manon", "olivier", "amelie",
        "jake", "ethan", "zoe", "victor", "lina", "gabriel", "chloe", "arthur", "nora", "samuel",
    ]
    last_names = [
        "dupont", "martin", "bernard", "thomas", "robert", "richard", "petit", "durand", "leroy", "moreau",
        "simon", "laurent", "lefebvre", "michel", "garcia", "david", "bertrand", "roux", "vincent", "fournier",
        "o'connor", "fitzgerald", "miller", "walker", "harris", "delaunay", "la-fontaine", "mc-arthur",
    ]

    meta_messages = [
        "je te l'ai déjà dit", "tu l'as déjà", "on vient d'en parler", "relis plus haut stp", "t'es sérieux là ?",
        "encore cette question ?", "tu fais exprès ?", "on boucle 😅", "je viens de répondre", "déjà mentionné",
    ]
    refusal_messages = [
        "je sais pas", "aucune idée", "j'en ai pas", "non", "nop", "nan", "pass", "je préfère pas dire",
        "impossible pour moi", "je ne peux pas répondre",
    ]
    typo_prefixes = ["mon pernom", "je m'appel", "prenon", "prénnom", "moi cest", "nomm", "prnom"]
    low_signal_messages = ["??", "...", "ok", "hein", "🙂", "🤷", "👍", "euh", "hmm", "lol", "ptdr", "😶"]
    toxic_messages = [
        "ta gueule", "ftg", "tg", "connard", "pauvre nul", "ferme-la", "dégage", "espèce d'idiot",
        "va te faire voir", "tu sers à rien",
    ]
    punctuation = ["", ".", "!", "!!", " ?", "...", " 🙃", " 😅"]

    id_counter = 1

    for _ in range(distribution["meta"]):
        missing = rng.choice(["first_name", "last_name", "birth_date"])
        if missing == "first_name":
            profile = {"first_name": "", "last_name": "Durand", "birth_date": "1994-07-11"}
            ask = ["prénom"]
        elif missing == "last_name":
            profile = {"first_name": "Camille", "last_name": "", "birth_date": "1994-07-11"}
            ask = ["nom de famille"]
        else:
            profile = {"first_name": "Camille", "last_name": "Durand", "birth_date": ""}
            ask = ["date de naissance"]
        message = rng.choice(meta_messages) + rng.choice(punctuation)
        scenarios.append(
            _one_turn_scenario(
                f"meta-{id_counter:04d}",
                profile,
                message,
                ask_contains=ask,
                no_reset=True,
            )
        )
        id_counter += 1

    for _ in range(distribution["refusal"]):
        missing = rng.choice(["first_name", "last_name", "birth_date"])
        if missing == "first_name":
            profile = {"first_name": "", "last_name": "Bernard", "birth_date": "1989-03-18"}
            ask = ["prénom"]
        elif missing == "last_name":
            profile = {"first_name": "Thomas", "last_name": "", "birth_date": "1989-03-18"}
            ask = ["nom de famille"]
        else:
            profile = {"first_name": "Thomas", "last_name": "Bernard", "birth_date": ""}
            ask = ["date de naissance"]
        scenarios.append(
            _one_turn_scenario(
                f"refusal-{id_counter:04d}",
                profile,
                rng.choice(refusal_messages) + rng.choice(punctuation),
                ask_contains=ask,
                no_reset=True,
            )
        )
        id_counter += 1

    for _ in range(distribution["typo"]):
        first_raw = rng.choice(first_names)
        typo = rng.choice(typo_prefixes)
        message = f"{typo} {first_raw}{rng.choice(punctuation)}"
        scenarios.append(
            _one_turn_scenario(
                f"typo-{id_counter:04d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["nom de famille"],
                update_equals={"first_name": _title_case_name(first_raw)},
            )
        )
        id_counter += 1

    for _ in range(distribution["mixed"]):
        first_raw = rng.choice(first_names)
        last_raw = rng.choice(last_names)
        year = rng.randint(1972, 2005)
        month = rng.randint(1, 12)
        day_max = 28 if month == 2 else 30 if month in {4, 6, 9, 11} else 31
        day = rng.randint(1, day_max)
        iso = _iso_from_parts(day, month, year)
        format_choice = rng.choice(["iso", "fr", "dots"])
        if format_choice == "iso":
            date_text = iso
        elif format_choice == "fr":
            date_text = f"{day:02d}/{month:02d}/{year}"
        else:
            date_text = f"{day:02d}.{month:02d}.{year}"
        intro = rng.choice(["", "je m'appelle ", "coucou, moi c'est "])
        message = f"{intro}{first_raw} {last_raw} {date_text}{rng.choice(punctuation)}".strip()
        scenarios.append(
            _one_turn_scenario(
                f"mixed-{id_counter:04d}",
                {"first_name": "", "last_name": "", "birth_date": ""},
                message,
                ask_contains=["récapitulatif", "tout est correct"],
                update_equals={
                    "first_name": _title_case_name(first_raw),
                    "last_name": _title_case_name(last_raw),
                    "birth_date": iso,
                },
            )
        )
        id_counter += 1

    for _ in range(distribution["low_signal"]):
        missing = rng.choice(["first_name", "last_name", "birth_date"])
        if missing == "first_name":
            profile = {"first_name": "", "last_name": "Miller", "birth_date": "1996-01-07"}
            ask = ["prénom"]
        elif missing == "last_name":
            profile = {"first_name": "Emma", "last_name": "", "birth_date": "1996-01-07"}
            ask = ["nom de famille"]
        else:
            profile = {"first_name": "Emma", "last_name": "Miller", "birth_date": ""}
            ask = ["date de naissance"]
        scenarios.append(
            _one_turn_scenario(
                f"low-signal-{id_counter:04d}",
                profile,
                rng.choice(low_signal_messages),
                ask_contains=ask,
                no_reset=True,
            )
        )
        id_counter += 1

    for _ in range(distribution["toxic"]):
        missing = rng.choice(["first_name", "last_name", "birth_date"])
        if missing == "first_name":
            profile = {"first_name": "", "last_name": "Walker", "birth_date": "1992-05-30"}
            ask = ["prénom"]
        elif missing == "last_name":
            profile = {"first_name": "Nicolas", "last_name": "", "birth_date": "1992-05-30"}
            ask = ["nom de famille"]
        else:
            profile = {"first_name": "Nicolas", "last_name": "Walker", "birth_date": ""}
            ask = ["date de naissance"]
        scenarios.append(
            _one_turn_scenario(
                f"toxic-{id_counter:04d}",
                profile,
                rng.choice(toxic_messages) + rng.choice(punctuation),
                ask_contains=ask,
                no_reset=True,
                no_llm=True,
            )
        )
        id_counter += 1

    month_names = [
        "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    for _ in range(distribution["birth_weird"]):
        first_raw = rng.choice(first_names)
        last_raw = rng.choice(last_names)
        year = rng.randint(1978, 2006)
        month = rng.randint(1, 12)
        day_max = 28 if month == 2 else 30 if month in {4, 6, 9, 11} else 31
        day = rng.randint(1, day_max)
        iso = _iso_from_parts(day, month, year)

        weird_kind = rng.choice(["year_typo", "impossible_date"])
        if weird_kind == "year_typo":
            year_text = f"{year}0" if rng.random() < 0.5 else f"{year}{rng.randint(0, 9)}"
            first_turn = {
                "user": f"Je suis né le {day} {month_names[month - 1]} {year_text}",
                "expect": {"ask_contains": ["confirmer", "année de naissance"]},
            }
            second_turn = {
                "user": "oui",
                "expect": {
                    "ask_contains": ["récapitulatif", "tout est correct"],
                    "update_equals": {"birth_date": iso},
                },
            }
        else:
            invalid_day = 31 if month in {4, 6, 9, 11} else 30
            first_turn = {
                "user": f"{invalid_day}/{month:02d}/{year}",
                "expect": {"ask_contains": ["date invalide", "date de naissance"]},
            }
            second_turn = {
                "user": f"{day:02d}/{month:02d}/{year}",
                "expect": {
                    "ask_contains": ["récapitulatif", "tout est correct"],
                    "update_equals": {"birth_date": iso},
                },
            }

        scenarios.append(
            {
                "id": f"birth-weird-{id_counter:04d}",
                "initial_profile": {
                    "first_name": _title_case_name(first_raw),
                    "last_name": _title_case_name(last_raw),
                    "birth_date": "",
                },
                "turns": [first_turn, second_turn],
            }
        )
        id_counter += 1

    rng.shuffle(scenarios)

    if len(scenarios) != count:
        raise RuntimeError(f"expected {count} scenarios, got {len(scenarios)}")

    ids = {scenario["id"] for scenario in scenarios}
    if len(ids) != count:
        raise RuntimeError("duplicate scenario ids detected")

    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate onboarding profile scenarios JSONL")
    parser.add_argument("--count", type=int, default=800, help="Number of scenarios to generate")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/profile_scenarios.jsonl"),
        help="Output JSONL file",
    )
    args = parser.parse_args()

    if args.count < 1:
        raise ValueError("count must be >= 1")

    scenarios = _build_scenarios(count=args.count, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for scenario in scenarios:
            handle.write(json.dumps(scenario, ensure_ascii=False) + "\n")
    print(f"wrote {len(scenarios)} scenarios to {args.output} (seed={args.seed})")


if __name__ == "__main__":
    main()
