"""Deterministic scenario fuzzing for onboarding profile collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import agent.api as agent_api
from agent.api import app
from tests.test_global_state_bootstrap import _LoopSpy, _Repo, _auth_headers, _mock_auth


client = TestClient(app)

_NO_RESET_BANNED_SUBSTRINGS = (
    "prénom et ton nom",
    "prénom, ton nom et ta date",
)


def _load_scenarios() -> list[dict[str, Any]]:
    fixture_path = Path("tests/fixtures/profile_scenarios.jsonl")
    scenarios: list[dict[str, Any]] = []
    for raw_line in fixture_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        scenarios.append(json.loads(line))
    return scenarios


def _assert_name_normalized(value: str) -> None:
    assert value == agent_api._format_person_name(value)


def test_profile_collect_fuzz_scenarios(monkeypatch) -> None:
    _mock_auth(monkeypatch)
    scenarios = _load_scenarios()
    assert len(scenarios) >= 80
    base_llm_extractor = agent_api._extract_profile_fields_with_llm

    for scenario in scenarios:
        repo = _Repo(
            initial_chat_state={
                "state": {
                    "global_state": {
                        "mode": "onboarding",
                        "onboarding_step": "profile",
                        "onboarding_substep": "profile_collect",
                    }
                }
            },
            profile_fields=dict(scenario["initial_profile"]),
        )
        monkeypatch.setattr(agent_api, "get_profiles_repository", lambda repo=repo: repo)
        monkeypatch.setattr(agent_api, "get_agent_loop", lambda: _LoopSpy())

        llm_calls = {"count": 0}
        def _counting_llm(message: str):
            llm_calls["count"] += 1
            return base_llm_extractor(message)

        monkeypatch.setattr(agent_api, "_extract_profile_fields_with_llm", _counting_llm)

        for turn in scenario["turns"]:
            expect = turn.get("expect", {})
            reply_before = llm_calls["count"]
            calls_before = len(repo.profile_update_calls)
            profile_before = dict(repo.profile_fields)
            expected_field = agent_api._next_missing_profile_field(profile_before)

            response = client.post("/agent/chat", json={"message": turn["user"]}, headers=_auth_headers())
            assert response.status_code == 200, scenario["id"]

            payload = response.json()
            reply = str(payload.get("reply") or "")
            reply_lower = reply.lower()

            for substring in expect.get("ask_contains", []):
                assert substring.lower() in reply_lower, f"{scenario['id']}: missing '{substring}'"

            if profile_before.get("first_name") and expect.get("no_reset"):
                assert all(chunk not in reply_lower for chunk in _NO_RESET_BANNED_SUBSTRINGS), scenario["id"]

            new_calls = repo.profile_update_calls[calls_before:]
            flattened_updates: dict[str, Any] = {}
            for call in new_calls:
                flattened_updates.update(call)

            if expected_field == "last_name":
                assert all("first_name" not in call for call in new_calls), scenario["id"]

            expected_updates = expect.get("update_equals") or {}
            for key, raw_value in expected_updates.items():
                expected_value = agent_api._format_person_name(raw_value) if key in {"first_name", "last_name"} else raw_value
                assert flattened_updates.get(key) == expected_value, scenario["id"]

            for call in new_calls:
                for key in ("first_name", "last_name"):
                    if isinstance(call.get(key), str) and call.get(key):
                        _assert_name_normalized(call[key])

            if expect.get("no_llm"):
                assert llm_calls["count"] == reply_before, scenario["id"]
