"""Onboarding loop for profile collection (form-driven)."""

from __future__ import annotations

from typing import Any

from agent.loops.types import LoopContext, LoopReply
from agent.onboarding.profile_recap import build_profile_recap_reply


_PROFILE_FIELDS: tuple[str, ...] = ("first_name", "last_name", "birth_date")


class OnboardingProfileCollectLoop:
    """Loop that waits for structured profile fields provided by the UI form."""

    id = "onboarding.profile_collect"
    blocking = True

    def can_enter(self, global_state: dict[str, Any], services: Any, profile_id: Any, user_id: Any) -> bool:
        if not isinstance(global_state, dict):
            return False
        return (
            global_state.get("mode") == "onboarding"
            and global_state.get("onboarding_substep") == "profile_collect"
        )

    def handle(
        self,
        message: str,
        ctx: LoopContext,
        *,
        services: Any,
        profile_id: Any,
        user_id: Any,
    ) -> LoopReply:
        profiles_repository = (services or {}).get("profiles_repository") if isinstance(services, dict) else None
        current_fields = self._current_fields(ctx=ctx, profiles_repository=profiles_repository, profile_id=profile_id)
        missing_slot = self._first_missing_slot(current_fields)

        next_ctx = (
            ctx
            if ctx.step != "start"
            else LoopContext(loop_id=ctx.loop_id, step="active", data=dict(ctx.data), blocking=ctx.blocking)
        )

        if missing_slot is None:
            updated_state = self._next_global_state(services, completed=True)
            updated_state["profile_confirmed"] = False
            profile_fields = self._current_fields(ctx=ctx, profiles_repository=profiles_repository, profile_id=profile_id)
            return LoopReply(
                reply=build_profile_recap_reply(profile_fields),
                next_loop=None,
                updates={"global_state": updated_state},
                handled=True,
            )

        return LoopReply(
            reply=self._ask_for_slot(missing_slot),
            next_loop=next_ctx,
            updates={"global_state": self._next_global_state(services, completed=False)},
            handled=True,
        )

    def expected_prompt_for_help(self, *, services: Any, profile_id: Any) -> str:
        """Return expected prompt for current profile_collect substep."""

        state_dict = (services or {}).get("state") if isinstance(services, dict) else None
        profiles_repository = (services or {}).get("profiles_repository") if isinstance(services, dict) else None
        probe_ctx = LoopContext(
            loop_id=self.id,
            step="active",
            data=dict(state_dict) if isinstance(state_dict, dict) else {},
            blocking=True,
        )
        current_fields = self._current_fields(ctx=probe_ctx, profiles_repository=profiles_repository, profile_id=profile_id)
        return self._ask_for_slot(self._first_missing_slot(current_fields))

    def _current_fields(self, *, ctx: LoopContext, profiles_repository: Any, profile_id: Any) -> dict[str, Any]:
        values = {field: None for field in _PROFILE_FIELDS}
        if isinstance(ctx.data, dict):
            for field in _PROFILE_FIELDS:
                values[field] = ctx.data.get(field)

        if profiles_repository is not None and hasattr(profiles_repository, "get_profile_fields"):
            try:
                from_repo = profiles_repository.get_profile_fields(profile_id=profile_id, fields=list(_PROFILE_FIELDS))
            except Exception:
                from_repo = {}
            if isinstance(from_repo, dict):
                for field in _PROFILE_FIELDS:
                    values[field] = from_repo.get(field) or values.get(field)
        return values

    def _first_missing_slot(self, values: dict[str, Any]) -> str | None:
        for field in _PROFILE_FIELDS:
            raw = values.get(field)
            if not isinstance(raw, str) or not raw.strip():
                return field
        return None

    def _ask_for_slot(self, slot: str | None) -> str:
        if slot in {"first_name", "last_name"}:
            return "Complète la carte profil avec ton prénom et ton nom pour continuer 🙂"
        if slot == "birth_date":
            return "Complète la carte profil avec ta date de naissance pour continuer 🙂"
        return "Complète la carte profil pour continuer 🙂"

    def _next_global_state(self, services: Any, *, completed: bool) -> dict[str, Any]:
        global_state = {}
        if isinstance(services, dict):
            global_state = dict(services.get("global_state") or {})
        global_state["mode"] = "onboarding"
        global_state["onboarding_step"] = "profile"
        global_state["onboarding_substep"] = "profile_confirm" if completed else "profile_collect"
        return global_state
