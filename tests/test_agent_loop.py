"""Tests for AgentLoop active_task prioritization."""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

import agent.loop
from agent.loop import AgentLoop
from agent.planner import NoopPlan, ToolCallPlan
from shared.models import ToolError, ToolErrorCode


class _FailIfCalledRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        raise AssertionError(f"Unexpected tool call: {tool_name} {payload}")


class _DeleteRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert tool_name == "finance_categories_delete"
        assert "category_name" in payload
        assert isinstance(payload["category_name"], str)
        assert payload["category_name"].strip().lower() == "autres"
        return {"ok": True}


class _DeleteBankAccountRouter:
    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        assert tool_name == "finance_bank_accounts_delete"
        assert payload == {"name": "Courant"}
        return {"ok": True}


class _AmbiguousThenDeleteByIdRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        if len(self.calls) == 1:
            assert tool_name == "finance_bank_accounts_delete"
            assert payload == {"name": "joint"}
            return ToolError(
                code=ToolErrorCode.AMBIGUOUS,
                message="Multiple bank accounts match the provided name.",
                details={
                    "name": "joint",
                    "candidates": [
                        {"id": "11111111-1111-1111-1111-111111111111", "name": "Joint"},
                        {"id": "22222222-2222-2222-2222-222222222222", "name": "JOINT"},
                    ],
                },
            )

        assert tool_name == "finance_bank_accounts_delete"
        assert payload == {"bank_account_id": "11111111-1111-1111-1111-111111111111"}
        return {"ok": True}


class _ListThenDeleteRouter:
    def __init__(self, items: list[dict[str, str]], *, can_delete: bool = True) -> None:
        self.items = items
        self.can_delete = can_delete
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        if tool_name == "finance_bank_accounts_list":
            return type(
                "_ListResult",
                (),
                {"items": [type("_Account", (), item) for item in self.items]},
            )()
        if tool_name == "finance_bank_accounts_can_delete":
            return {"ok": True, "can_delete": self.can_delete}
        if tool_name == "finance_bank_accounts_delete":
            return {"ok": True}
        raise AssertionError(f"Unexpected tool call: {tool_name}")


class _NotFoundSuggestionRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        if len(self.calls) == 1:
            assert tool_name == "finance_bank_accounts_delete"
            assert payload == {"name": "vacnces"}
            return ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message="Bank account not found for provided name.",
                details={"name": "vacnces", "close_names": ["Compte vacances"]},
            )

        assert tool_name == "finance_bank_accounts_delete"
        assert payload == {"name": "Compte vacances"}
        return {"ok": True}


class _SearchRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        assert tool_name == "finance_releves_search"
        return {"ok": True, "items": []}


class _SearchWithBankHintRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        if tool_name == "finance_bank_accounts_list":
            return type(
                "_ListResult",
                (),
                {
                    "items": [
                        type("_Account", (), {"id": "acc-ubs", "name": "UBS"}),
                        type(
                            "_Account",
                            (),
                            {"id": "acc-credit-suisse", "name": "Credit Suisse"},
                        ),
                        type("_Account", (), {"id": "acc-revolut", "name": "Revolut"}),
                    ]
                },
            )()

        assert tool_name == "finance_releves_search"
        return {"ok": True, "items": []}


class _SearchWithMissingBankHintRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        if tool_name == "finance_bank_accounts_list":
            return type(
                "_ListResult",
                (),
                {
                    "items": [
                        type("_Account", (), {"id": "acc-ubs", "name": "UBS"}),
                        type("_Account", (), {"id": "acc-neon", "name": "Neon"}),
                    ]
                },
            )()

        assert tool_name == "finance_releves_search"
        return {"ok": True, "items": []}


class _ProfileUpdateRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
        self.calls.append((tool_name, payload))
        assert tool_name == "finance_profile_update"
        return {"ok": True}


def test_confirm_delete_category_yes_executes_delete() -> None:
    loop = AgentLoop(tool_router=_DeleteRouter())

    reply = loop.handle_user_message(
        "Oui",
        active_task={
            "type": "needs_confirmation",
            "confirmation_type": "confirm_delete_category",
            "context": {"category_name": "autres"},
        },
    )

    assert reply.plan == {
        "tool_name": "finance_categories_delete",
        "payload": {"category_name": "autres"},
    }
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_category_no_cancels() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message(
        "non",
        active_task={
            "type": "needs_confirmation",
            "confirmation_type": "confirm_delete_category",
            "context": {"category_name": "autres"},
        },
    )

    assert reply.reply == "Suppression annulée."
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_category_invalid_prompts_again() -> None:
    active_task = {
        "type": "needs_confirmation",
        "confirmation_type": "confirm_delete_category",
        "context": {"category_name": "autres"},
    }
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("peut-être", active_task=active_task)

    assert reply.reply == "Répondez OUI ou NON."
    assert reply.should_update_active_task is True
    assert reply.active_task == active_task


def test_confirm_delete_bank_account_yes_executes_delete() -> None:
    loop = AgentLoop(tool_router=_DeleteBankAccountRouter())

    reply = loop.handle_user_message(
        "oui",
        active_task={
            "type": "needs_confirmation",
            "confirmation_type": "confirm_delete_bank_account",
            "context": {"name": "Courant"},
        },
    )

    assert reply.plan == {
        "tool_name": "finance_bank_accounts_delete",
        "payload": {"name": "Courant"},
    }
    assert reply.should_update_active_task is True
    assert reply.active_task is None



@pytest.mark.parametrize("message", ["delete le compte test", "remove le compte test"])
def test_bank_account_delete_variants_stay_deterministic_and_execute_after_confirmation(
    monkeypatch, message: str
) -> None:
    calls = {"llm": 0}

    def _spy_plan_from_message(*_args, **_kwargs):
        calls["llm"] += 1
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "should-not-run"},
            user_reply="OK.",
        )

    class _DeleteTestAccountRouter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, tool_name: str, payload: dict, *, profile_id: UUID | None = None):
            self.calls.append((tool_name, payload))
            assert tool_name == "finance_bank_accounts_delete"
            assert payload == {"name": "test"}
            return {"ok": True}

    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_search"})
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    router = _DeleteTestAccountRouter()
    loop = AgentLoop(tool_router=router, llm_planner=object())

    confirm_reply = loop.handle_user_message(message)

    assert confirm_reply.should_update_active_task is True
    assert confirm_reply.active_task is not None
    assert confirm_reply.active_task["type"] == "needs_confirmation"
    assert (
        confirm_reply.active_task["confirmation_type"]
        == "confirm_delete_bank_account"
    )
    assert confirm_reply.active_task["context"] == {"name": "test"}
    assert calls["llm"] == 0

    delete_reply = loop.handle_user_message("oui", active_task=confirm_reply.active_task)

    assert delete_reply.plan == {
        "tool_name": "finance_bank_accounts_delete",
        "payload": {"name": "test"},
    }
    assert calls["llm"] == 0
    assert router.calls == [("finance_bank_accounts_delete", {"name": "test"})]


def test_bank_account_ambiguous_sets_select_active_task_then_resolves_by_index() -> (
    None
):
    router = _AmbiguousThenDeleteByIdRouter()
    loop = AgentLoop(tool_router=router)

    confirm_reply = loop.handle_user_message("supprime le compte joint")

    assert confirm_reply.should_update_active_task is True
    assert confirm_reply.active_task is not None
    assert confirm_reply.active_task["type"] == "needs_confirmation"
    assert (
        confirm_reply.active_task["confirmation_type"] == "confirm_delete_bank_account"
    )
    assert confirm_reply.active_task["context"] == {"name": "joint"}

    first_reply = loop.handle_user_message("oui", active_task=confirm_reply.active_task)

    assert first_reply.should_update_active_task is True
    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "select_bank_account"
    assert (
        first_reply.active_task["original_tool_name"] == "finance_bank_accounts_delete"
    )
    assert first_reply.active_task["original_payload"] == {"name": "joint"}
    assert first_reply.reply == (
        "Plusieurs comptes correspondent: Joint, JOINT. Répondez avec le nom exact (ou 1/2)."
    )

    second_reply = loop.handle_user_message("1", active_task=first_reply.active_task)

    assert second_reply.should_update_active_task is True
    assert second_reply.active_task is None
    assert second_reply.plan == {
        "tool_name": "finance_bank_accounts_delete",
        "payload": {"bank_account_id": "11111111-1111-1111-1111-111111111111"},
    }


def test_bank_account_not_found_suggestion_yes_replays_with_first_name() -> None:
    router = _NotFoundSuggestionRouter()
    loop = AgentLoop(tool_router=router)

    confirm_reply = loop.handle_user_message("supprime le compte vacnces")

    assert confirm_reply.should_update_active_task is True
    assert confirm_reply.active_task is not None
    assert confirm_reply.active_task["type"] == "needs_confirmation"
    assert (
        confirm_reply.active_task["confirmation_type"] == "confirm_delete_bank_account"
    )
    assert confirm_reply.active_task["context"] == {"name": "vacnces"}

    first_reply = loop.handle_user_message("oui", active_task=confirm_reply.active_task)

    assert first_reply.should_update_active_task is True
    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "select_bank_account"
    assert first_reply.active_task["suggestions"] == ["Compte vacances"]
    assert first_reply.reply == (
        "Je ne trouve pas le compte « vacnces ». Vouliez-vous dire: Compte vacances ? "
        "Répondez par le nom exact ou OUI pour choisir le premier."
    )

    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert second_reply.should_update_active_task is True
    assert second_reply.active_task is None
    assert second_reply.plan == {
        "tool_name": "finance_bank_accounts_delete",
        "payload": {"name": "Compte vacances"},
    }


def test_confirm_delete_bank_account_not_found_skips_confirmation_when_profile_available() -> (
    None
):
    router = _ListThenDeleteRouter(
        items=[{"id": "11111111-1111-1111-1111-111111111111", "name": "UBS"}]
    )
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "supprime le compte Inexistant",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert reply.reply == "Je ne trouve pas le compte « Inexistant »."
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_bank_account_conflict_when_not_empty_skips_confirmation() -> (
    None
):
    router = _ListThenDeleteRouter(
        items=[{"id": "11111111-1111-1111-1111-111111111111", "name": "UBS"}],
        can_delete=False,
    )
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "supprime le compte ubs",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert (
        reply.reply
        == "Impossible de supprimer ce compte car il contient des transactions. "
        "Déplacez/supprimez d’abord les transactions ou choisissez un autre compte."
    )
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_confirm_delete_bank_account_existing_stores_id_in_active_task() -> None:
    router = _ListThenDeleteRouter(
        items=[{"id": "11111111-1111-1111-1111-111111111111", "name": "UBS"}],
        can_delete=True,
    )
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "supprime le compte ubs",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert (
        reply.reply
        == "Confirmez-vous la suppression du compte « UBS » ? Répondez OUI ou NON."
    )
    assert reply.should_update_active_task is True
    assert reply.active_task is not None
    assert reply.active_task["type"] == "needs_confirmation"
    assert reply.active_task["confirmation_type"] == "confirm_delete_bank_account"
    assert reply.active_task["context"] == {
        "name": "UBS",
        "bank_account_id": "11111111-1111-1111-1111-111111111111",
    }

    confirmation = loop.handle_user_message("oui", active_task=reply.active_task)

    assert confirmation.plan == {
        "tool_name": "finance_bank_accounts_delete",
        "payload": {"bank_account_id": "11111111-1111-1111-1111-111111111111"},
    }


def test_nlu_ui_action_open_import_panel_returns_structured_tool_result() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("je veux importer un relevé")

    assert reply.reply == "D'accord, j'ouvre le panneau d'import de relevés."
    assert reply.tool_result == {"type": "ui_action", "action": "open_import_panel"}


def test_nlu_tool_call_executes_before_deterministic_planner() -> None:
    class _CreateAccountRouter:
        def call(
            self, tool_name: str, payload: dict, *, profile_id: UUID | None = None
        ):
            assert tool_name == "finance_bank_accounts_create"
            assert payload == {"name": "UBS"}
            return {"id": "new-account"}

    loop = AgentLoop(tool_router=_CreateAccountRouter())

    reply = loop.handle_user_message("Nouveau compte: UBS")

    assert reply.plan == {
        "tool_name": "finance_bank_accounts_create",
        "payload": {"name": "UBS"},
    }
    assert reply.tool_result == {"id": "new-account"}


@pytest.mark.parametrize(
    ("message", "expected_payload"),
    [
        ("Mets à jour mon profil : ville Choëx", {"set": {"city": "Choëx"}}),
        (
            "Mets à jour mon profil : code postal 1897",
            {"set": {"postal_code": "1897"}},
        ),
    ],
)
def test_nlu_profile_update_requires_confirmation_before_execution(
    message: str,
    expected_payload: dict[str, dict[str, str]],
) -> None:
    router = _ProfileUpdateRouter()
    loop = AgentLoop(tool_router=router)

    confirm_reply = loop.handle_user_message(message)

    assert confirm_reply.reply.startswith("Je peux mettre à jour votre profil")
    assert confirm_reply.should_update_active_task is True
    assert confirm_reply.active_task is not None
    assert confirm_reply.active_task["type"] == "needs_confirmation"
    assert confirm_reply.active_task["confirmation_type"] == "confirm_llm_write"
    assert confirm_reply.active_task["context"] == {
        "tool_name": "finance_profile_update",
        "payload": expected_payload,
    }

    accepted_reply = loop.handle_user_message(
        "oui", active_task=confirm_reply.active_task
    )
    assert router.calls == [("finance_profile_update", expected_payload)]
    assert accepted_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": expected_payload,
    }

    cancel_reply = loop.handle_user_message(
        "non", active_task=confirm_reply.active_task
    )
    assert cancel_reply.reply == "Action annulée."


@pytest.mark.parametrize(
    ("message", "expected_payload"),
    [
        ("Ma ville est Choëx", {"set": {"city": "Choëx"}}),
        ("Mon code postal est 1871", {"set": {"postal_code": "1871"}}),
    ],
)
def test_deterministic_profile_write_requires_confirmation_before_execution(
    message: str,
    expected_payload: dict[str, dict[str, str]],
) -> None:
    router = _ProfileUpdateRouter()
    loop = AgentLoop(tool_router=router)

    first_reply = loop.handle_user_message(message)

    assert router.calls == []
    assert first_reply.active_task is not None
    assert first_reply.active_task["type"] == "needs_confirmation"
    assert first_reply.active_task["confirmation_type"] == "confirm_llm_write"
    assert first_reply.active_task["context"] == {
        "tool_name": "finance_profile_update",
        "payload": expected_payload,
    }

    second_reply = loop.handle_user_message("oui", active_task=first_reply.active_task)

    assert router.calls == [("finance_profile_update", expected_payload)]
    assert second_reply.plan == {
        "tool_name": "finance_profile_update",
        "payload": expected_payload,
    }


def test_deterministic_delete_category_keeps_dedicated_confirmation() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("supprime la catégorie autres")

    assert reply.active_task is not None
    assert reply.active_task["type"] == "needs_confirmation"
    assert reply.active_task["confirmation_type"] == "confirm_delete_category"


def test_nlu_search_without_merchant_sets_active_task_with_date_range() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("recherche en janvier 2026")

    assert reply.reply == "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?"
    assert reply.tool_result == {
        "type": "clarification",
        "clarification_type": "awaiting_search_merchant",
        "message": "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
        "payload": {
            "date_range": {
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 31),
            }
        },
    }
    assert reply.should_update_active_task is True
    assert reply.active_task == {
        "type": "awaiting_search_merchant",
        "date_range": {"start_date": date(2026, 1, 1), "end_date": date(2026, 1, 31)},
    }


def test_nlu_search_without_merchant_starts_active_task_via_plan_meta() -> None:
    loop = AgentLoop(tool_router=_FailIfCalledRouter())

    reply = loop.handle_user_message("recherche en janvier 2026")

    assert reply.reply == "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?"
    assert reply.tool_result == {
        "type": "clarification",
        "clarification_type": "awaiting_search_merchant",
        "message": "Que voulez-vous rechercher (ex: Migros, coffee, Coop) ?",
        "payload": {
            "date_range": {
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 31),
            }
        },
    }
    assert reply.active_task == {
        "type": "awaiting_search_merchant",
        "date_range": {"start_date": date(2026, 1, 1), "end_date": date(2026, 1, 31)},
    }
    assert reply.should_update_active_task is True


def test_active_task_search_merchant_runs_search_and_clears_active_task() -> None:
    router = _SearchRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Coop",
        active_task={
            "type": "awaiting_search_merchant",
            "date_range": {
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 31),
            },
        },
    )

    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "coop",
            "limit": 50,
            "offset": 0,
            "date_range": {
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 31),
            },
        },
    }
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_active_task_search_merchant_without_date_range_runs_search() -> None:
    router = _SearchRouter()
    loop = AgentLoop(tool_router=router)

    first_reply = loop.handle_user_message("cherche")
    second_reply = loop.handle_user_message(
        "Migros", active_task=first_reply.active_task
    )

    assert first_reply.should_update_active_task is True
    assert first_reply.active_task == {"type": "awaiting_search_merchant"}
    assert second_reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "migros", "limit": 50, "offset": 0},
    }
    assert second_reply.should_update_active_task is True
    assert second_reply.active_task is None


def test_nlu_search_with_known_bank_hint_adds_bank_account_id() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros UBS",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {"merchant": "migros", "limit": 50, "offset": 0, "bank_account_id": "acc-ubs"},
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-ubs",
        },
    }


def test_nlu_search_with_unknown_bank_hint_keeps_merchant_without_bank_account_id() -> (
    None
):
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros UnknownBank",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls == [
        (
            "finance_releves_search",
            {"merchant": "migros unknownbank", "limit": 50, "offset": 0},
        ),
    ]
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "migros unknownbank", "limit": 50, "offset": 0},
    }


def test_nlu_search_with_credit_suisse_hint_without_account_uses_merchant_fallback() -> (
    None
):
    router = _SearchWithMissingBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros crédit suisse",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {"merchant": "migros crédit suisse", "limit": 50, "offset": 0},
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "migros crédit suisse", "limit": 50, "offset": 0},
    }


def test_nlu_search_with_revolut_pro_hint_without_account_uses_merchant_fallback() -> (
    None
):
    router = _SearchWithMissingBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros revolut pro",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {"merchant": "migros revolut pro", "limit": 50, "offset": 0},
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "migros revolut pro", "limit": 50, "offset": 0},
    }


def test_nlu_search_with_punctuated_bank_hint_matches_account() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros UBS!!!",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {"merchant": "migros", "limit": 50, "offset": 0, "bank_account_id": "acc-ubs"},
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-ubs",
        },
    }


def test_nlu_search_with_multi_word_bank_hint_matches_account() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros credit suisse",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    }


def test_nlu_search_with_accented_bank_hint_matches_account() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros crédit suisse",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    }


def test_nlu_search_with_hyphenated_bank_hint_matches_account() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros credit-suisse",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-credit-suisse",
        },
    }


def test_nlu_search_with_revolut_pro_hint_matches_account() -> None:
    router = _SearchWithBankHintRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "cherche Migros revolut pro",
        profile_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )

    assert router.calls[0] == ("finance_bank_accounts_list", {})
    assert router.calls[1] == (
        "finance_releves_search",
        {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-revolut",
        },
    )
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "migros",
            "limit": 50,
            "offset": 0,
            "bank_account_id": "acc-revolut",
        },
    }


def test_active_task_search_merchant_with_serialized_dates_runs_search() -> None:
    router = _SearchRouter()
    loop = AgentLoop(tool_router=router)

    reply = loop.handle_user_message(
        "Coop",
        active_task={
            "type": "awaiting_search_merchant",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
    )

    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "coop",
            "limit": 50,
            "offset": 0,
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
    }
    assert reply.should_update_active_task is True
    assert reply.active_task is None


def test_nlu_tool_call_with_llm_planner_does_not_call_plan_from_message(
    monkeypatch,
) -> None:
    class _CreateAccountRouter:
        def call(
            self, tool_name: str, payload: dict, *, profile_id: UUID | None = None
        ):
            assert tool_name == "finance_bank_accounts_create"
            assert payload == {"name": "UBS"}
            return {"id": "new-account"}

    def _fake_parse_intent(message: str):
        assert message == "ignored"
        return {
            "type": "tool_call",
            "tool_name": "finance_bank_accounts_create",
            "payload": {"name": "UBS"},
        }

    def _fail_plan_from_message(*args, **kwargs):
        raise AssertionError(
            "plan_from_message should not be called when deterministic NLU returns a tool_call"
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(agent.loop, "plan_from_message", _fail_plan_from_message)

    loop = AgentLoop(tool_router=_CreateAccountRouter(), llm_planner=object())
    reply = loop.handle_user_message("ignored")

    assert reply.plan == {
        "tool_name": "finance_bank_accounts_create",
        "payload": {"name": "UBS"},
    }
    assert reply.tool_result == {"id": "new-account"}


def test_nlu_tool_call_with_llm_shadow_enabled_calls_plan_from_message_once(
    monkeypatch,
) -> None:
    class _CreateAccountRouter:
        def call(
            self, tool_name: str, payload: dict, *, profile_id: UUID | None = None
        ):
            assert tool_name == "finance_bank_accounts_create"
            assert payload == {"name": "UBS"}
            return {"id": "new-account"}

    def _fake_parse_intent(message: str):
        assert message == "ignored"
        return {
            "type": "tool_call",
            "tool_name": "finance_bank_accounts_create",
            "payload": {"name": "UBS"},
        }

    calls = {"count": 0}

    def _spy_plan_from_message(*_args, **_kwargs):
        calls["count"] += 1
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "shadow"},
            user_reply="OK.",
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)

    loop = AgentLoop(
        tool_router=_CreateAccountRouter(),
        llm_planner=object(),
        shadow_llm=True,
    )
    reply = loop.handle_user_message("ignored")

    assert calls["count"] == 1
    assert reply.plan == {
        "tool_name": "finance_bank_accounts_create",
        "payload": {"name": "UBS"},
    }
    assert reply.tool_result == {"id": "new-account"}


def test_llm_execution_skipped_when_llm_disabled_even_with_planner(monkeypatch) -> None:
    def _fake_parse_intent(_message: str):
        return None

    calls = {"count": 0}

    def _spy_plan_from_message(*_args, **_kwargs):
        calls["count"] += 1
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "coop"},
            user_reply="OK.",
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(agent.loop, "deterministic_plan_from_message", lambda _m: NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'."))
    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: False)
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    loop = AgentLoop(tool_router=_FailIfCalledRouter(), llm_planner=object())
    reply = loop.handle_user_message("ignored")

    assert calls["count"] == 0
    assert reply.reply == "Commandes disponibles: 'ping' ou 'search: <term>'."


def test_llm_gated_disallows_tool_outside_allowlist(monkeypatch) -> None:
    def _fake_parse_intent(_message: str):
        return None

    calls = {"count": 0}

    def _spy_plan_from_message(*_args, **_kwargs):
        calls["count"] += 1
        return ToolCallPlan(
            tool_name="finance_bank_accounts_delete",
            payload={"name": "Courant"},
            user_reply="OK.",
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(agent.loop, "deterministic_plan_from_message", lambda _m: NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'."))
    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_search"})
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    loop = AgentLoop(tool_router=_FailIfCalledRouter(), llm_planner=object())
    reply = loop.handle_user_message("ignored")

    assert calls["count"] == 1
    assert reply.reply == "Commandes disponibles: 'ping' ou 'search: <term>'."


def test_llm_gated_executes_allowed_tool(monkeypatch) -> None:
    router = _SearchRouter()

    def _fake_parse_intent(_message: str):
        return None

    def _spy_plan_from_message(*_args, **_kwargs):
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload={"merchant": "coop"},
            user_reply="OK.",
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(agent.loop, "deterministic_plan_from_message", lambda _m: NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'."))
    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_search"})
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    loop = AgentLoop(tool_router=router, llm_planner=object())
    reply = loop.handle_user_message("ignored")

    assert router.calls == [
        (
            "finance_releves_search",
            {"merchant": "coop", "limit": 50, "offset": 0},
        )
    ]
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {"merchant": "coop", "limit": 50, "offset": 0},
    }


def test_llm_gated_executes_allowed_bank_accounts_list_tool_when_deterministic_is_noop(
    monkeypatch,
) -> None:
    class _ListAccountsRouter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(
            self, tool_name: str, payload: dict, *, profile_id: UUID | None = None
        ):
            self.calls.append((tool_name, payload))
            assert tool_name == "finance_bank_accounts_list"
            return {
                "items": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "profile_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "name": "UBS",
                        "kind": "individual",
                        "account_kind": "personal_current",
                        "is_system": False,
                    }
                ],
                "default_bank_account_id": "11111111-1111-1111-1111-111111111111",
            }

    def _fake_parse_intent(_message: str):
        return None

    def _spy_plan_from_message(*_args, **_kwargs):
        return ToolCallPlan(
            tool_name="finance_bank_accounts_list",
            payload={},
            user_reply="",
        )

    monkeypatch.setattr(agent.loop, "parse_intent", _fake_parse_intent)
    monkeypatch.setattr(
        agent.loop,
        "deterministic_plan_from_message",
        lambda _m: NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'."),
    )
    monkeypatch.setattr(agent.loop, "plan_from_message", _spy_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(
        agent.loop.config,
        "llm_allowed_tools",
        lambda: {"finance_bank_accounts_list"},
    )
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    router = _ListAccountsRouter()
    loop = AgentLoop(tool_router=router, llm_planner=object())
    reply = loop.handle_user_message("Montre moi mes comptes bancaires")

    assert router.calls == [
        (
            "finance_bank_accounts_list",
            {},
        )
    ]
    assert reply.plan == {
        "tool_name": "finance_bank_accounts_list",
        "payload": {},
    }


def test_active_task_never_runs_llm_execution_when_gated(monkeypatch) -> None:
    def _fail_plan_from_message(*_args, **_kwargs):
        raise AssertionError("LLM execution should not run when active_task is present")

    monkeypatch.setattr(agent.loop, "plan_from_message", _fail_plan_from_message)
    monkeypatch.setattr(agent.loop.config, "llm_enabled", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_gated", lambda: True)
    monkeypatch.setattr(agent.loop.config, "llm_allowed_tools", lambda: {"finance_releves_search"})
    monkeypatch.setattr(agent.loop.config, "llm_shadow", lambda: False)

    router = _SearchRouter()
    loop = AgentLoop(tool_router=router, llm_planner=object())
    reply = loop.handle_user_message(
        "Coop",
        active_task={
            "type": "awaiting_search_merchant",
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
    )

    assert router.calls == [
        (
            "finance_releves_search",
            {
                "merchant": "coop",
                "limit": 50,
                "offset": 0,
                "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
            },
        )
    ]
    assert reply.plan == {
        "tool_name": "finance_releves_search",
        "payload": {
            "merchant": "coop",
            "limit": 50,
            "offset": 0,
            "date_range": {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        },
    }
