"""Default onboarding and household loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.loops.onboarding_profile import OnboardingProfileCollectLoop
from agent.loops.types import LoopContext, LoopReply


@dataclass(slots=True)
class DeterministicLoop:
    id: str
    blocking: bool
    prompt: str
    expected_values: tuple[str, ...] = ()
    enter_when_substeps: tuple[str, ...] = ()
    next_loop_id: str | None = None
    allow_fallback: bool = False

    def can_enter(self, global_state: dict[str, Any], services: Any, profile_id: Any, user_id: Any) -> bool:
        if not isinstance(global_state, dict) or global_state.get("mode") != "onboarding":
            return False
        if not self.enter_when_substeps:
            return False
        active_substep = global_state.get("onboarding_substep")
        return active_substep in self.enter_when_substeps

    def handle(self, message: str, ctx: LoopContext, *, services: Any, profile_id: Any, user_id: Any) -> LoopReply:
        if self.allow_fallback:
            return LoopReply(reply="", next_loop=ctx, handled=False)

        lowered = message.strip().lower()
        if self.expected_values and lowered not in self.expected_values:
            allowed_values = " ou ".join(self.expected_values)
            return LoopReply(
                reply=f"Je n’ai pas compris. Réponds par: {allowed_values}.\n\n{self.prompt}",
                next_loop=ctx,
                handled=True,
            )

        next_loop = None
        reply_text = self.prompt
        if self.next_loop_id is not None:
            next_loop = LoopContext(loop_id=self.next_loop_id, step="start", data=dict(ctx.data), blocking=self.blocking)
            reply_text = "Parfait ✅ Passons à l’étape suivante."
        return LoopReply(reply=reply_text, next_loop=next_loop, updates={"last_message": message}, handled=True)


@dataclass(slots=True)
class OnboardingProfileFixSelectLoop:
    """Handle profile correction target selection after a negative confirmation."""

    id: str = "onboarding.profile_fix_select"
    blocking: bool = True

    def can_enter(self, global_state: dict[str, Any], services: Any, profile_id: Any, user_id: Any) -> bool:
        if not isinstance(global_state, dict):
            return False
        return (
            global_state.get("mode") == "onboarding"
            and global_state.get("onboarding_substep") == "profile_fix_select"
        )

    def handle(self, message: str, ctx: LoopContext, *, services: Any, profile_id: Any, user_id: Any) -> LoopReply:
        normalized = message.strip().lower()
        profiles_repository = (services or {}).get("profiles_repository") if isinstance(services, dict) else None
        current_global_state = (services or {}).get("global_state") if isinstance(services, dict) else {}

        if normalized in {"corriger_nom", "name"}:
            if profiles_repository is not None and hasattr(profiles_repository, "update_profile_fields"):
                profiles_repository.update_profile_fields(
                    profile_id=profile_id,
                    set_dict={"first_name": "", "last_name": ""},
                )
            updated_global_state = dict(current_global_state) if isinstance(current_global_state, dict) else {}
            updated_global_state.update(
                {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                    "profile_confirmed": False,
                }
            )
            return LoopReply(
                reply="Ok 🙂 Mets à jour prénom/nom dans la carte profil.",
                next_loop=None,
                updates={"global_state": updated_global_state},
                handled=True,
            )

        if normalized in {"corriger_date", "birth_date"}:
            if profiles_repository is not None and hasattr(profiles_repository, "update_profile_fields"):
                profiles_repository.update_profile_fields(
                    profile_id=profile_id,
                    set_dict={"birth_date": ""},
                )
            updated_global_state = dict(current_global_state) if isinstance(current_global_state, dict) else {}
            updated_global_state.update(
                {
                    "mode": "onboarding",
                    "onboarding_step": "profile",
                    "onboarding_substep": "profile_collect",
                    "profile_confirmed": False,
                }
            )
            return LoopReply(
                reply="Ok 🙂 Mets à jour la date de naissance dans la carte profil.",
                next_loop=None,
                updates={"global_state": updated_global_state},
                handled=True,
            )

        return LoopReply(reply="Choisis une option proposée (prénom/nom ou date de naissance).", next_loop=ctx, updates={}, handled=True)


def build_default_loops() -> list[Any]:
    yes_no = ("oui", "non", "✅", "❌")
    return [
        OnboardingProfileCollectLoop(),
        OnboardingProfileFixSelectLoop(),
        DeterministicLoop("onboarding.profile_confirm", True, "Confirme ton profil (oui/non).", yes_no, ("profile_confirm",), "onboarding.bank_accounts_collect"),
        DeterministicLoop("onboarding.bank_accounts_collect", True, "Sélectionne les banques où tu as un compte. Tu peux en choisir plusieurs.", enter_when_substeps=("bank_accounts_collect",)),
        DeterministicLoop("onboarding.bank_accounts_confirm", True, "Confirme la liste des comptes (oui/non).", yes_no, ("bank_accounts_confirm",), "onboarding.import_select_account"),
        DeterministicLoop("onboarding.import_select_account", True, "Sélectionne le compte à importer.", enter_when_substeps=("import_select_account",)),
        DeterministicLoop("onboarding.import_wait_ready", True, "Ton CSV est-il prêt ? (oui/non)", yes_no, ("import_wait_ready",), "onboarding.categories_intro"),
        DeterministicLoop("onboarding.categories_intro", True, "On va préparer tes catégories personnalisées.", enter_when_substeps=("categories_intro",)),
        DeterministicLoop("onboarding.categories_bootstrap", True, "Je crée les catégories système.", enter_when_substeps=("categories_bootstrap",)),
        DeterministicLoop("onboarding.report", False, "Es-tu prêt à voir ton premier rapport ?", yes_no, ("report_offer", "report_sent"), None, True),
        DeterministicLoop("household_link.setup", False, "As-tu des dépenses communes à partager ? (oui/non)", yes_no),
    ]
