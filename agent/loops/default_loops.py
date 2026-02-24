"""Default onboarding and household loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.loops.types import LoopContext, LoopReply


@dataclass(slots=True)
class DeterministicLoop:
    id: str
    blocking: bool
    prompt: str
    expected_values: tuple[str, ...] = ()
    next_loop_id: str | None = None

    def can_enter(self, global_state: dict[str, Any], services: Any, profile_id: Any, user_id: Any) -> bool:
        active_substep = global_state.get("onboarding_substep") if isinstance(global_state, dict) else None
        return active_substep is not None and self.id.endswith(str(active_substep))

    def handle(self, message: str, ctx: LoopContext, *, services: Any, profile_id: Any, user_id: Any) -> LoopReply:
        lowered = message.strip().lower()
        if self.expected_values and lowered not in self.expected_values:
            return LoopReply(reply=self.prompt, next_loop=ctx, handled=False)

        next_loop = None
        if self.next_loop_id is not None:
            next_loop = LoopContext(loop_id=self.next_loop_id, step="start", data=dict(ctx.data), blocking=self.blocking)
        return LoopReply(reply=self.prompt, next_loop=next_loop, updates={"last_message": message}, handled=True)


def build_default_loops() -> list[DeterministicLoop]:
    yes_no = ("oui", "non")
    return [
        DeterministicLoop("onboarding.profile_collect", True, "Donne ton prénom, nom et date de naissance (YYYY-MM-DD)."),
        DeterministicLoop("onboarding.profile_confirm", True, "Confirme ton profil (oui/non).", yes_no, "onboarding.bank_accounts_collect"),
        DeterministicLoop("onboarding.bank_accounts_collect", True, "Quels comptes utilises-tu ?"),
        DeterministicLoop("onboarding.bank_accounts_confirm", True, "Confirme la liste des comptes (oui/non).", yes_no, "onboarding.import_select_account"),
        DeterministicLoop("onboarding.import_select_account", True, "Sélectionne le compte à importer."),
        DeterministicLoop("onboarding.import_wait_ready", True, "Ton CSV est-il prêt ? (oui/non)", yes_no, "onboarding.categories_intro"),
        DeterministicLoop("onboarding.categories_intro", True, "On va préparer tes catégories personnalisées."),
        DeterministicLoop("onboarding.categories_bootstrap", True, "Je crée les catégories système."),
        DeterministicLoop("onboarding.report", False, "Veux-tu générer ton premier rapport ? (oui/non)", yes_no),
        DeterministicLoop("household_link.setup", False, "As-tu des dépenses communes à partager ? (oui/non)", yes_no),
    ]
