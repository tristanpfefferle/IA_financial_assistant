"""Smart onboarding loop for profile collection."""

from __future__ import annotations

from typing import Any

from agent.loops.confidence import ConfidenceLevel, parse_profile_collect_message
from agent.loops.types import LoopContext, LoopReply


_PROFILE_FIELDS: tuple[str, ...] = ("first_name", "last_name", "birth_date")


class OnboardingProfileCollectLoop:
    """Deterministic smart loop that collects missing profile slots."""

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
        if missing_slot is None:
            if ctx.step == "start":
                return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)
            updated_state = self._next_global_state(services, completed=True)
            return LoopReply(
                reply="Top, ton profil est complet. Tu confirmes ces informations ?",
                next_loop=ctx,
                updates={"global_state": updated_state},
                handled=True,
            )

        parsed = parse_profile_collect_message(message)
        slot_value = self._pick_slot_value(missing_slot, parsed, current_fields)
        if slot_value is None:
            return LoopReply(reply=self._ask_for_slot(missing_slot), next_loop=ctx, updates={}, handled=True)

        should_fallback_to_legacy = ctx.step == "start" and missing_slot == "first_name"
        if should_fallback_to_legacy and not self._is_safe_start_capture(missing_slot, parsed):
            return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)

        if slot_value.confidence == ConfidenceLevel.HIGH and slot_value.value:
            if profiles_repository is not None and hasattr(profiles_repository, "update_profile_fields"):
                try:
                    profiles_repository.update_profile_fields(
                        profile_id=profile_id,
                        user_id=user_id,
                        set_dict={missing_slot: slot_value.value},
                    )
                except TypeError:
                    profiles_repository.update_profile_fields(
                        profile_id=profile_id,
                        set_dict={missing_slot: slot_value.value},
                    )
            current_fields[missing_slot] = slot_value.value
            completed = self._first_missing_slot(current_fields) is None
            updated_state = self._next_global_state(services, completed=completed)
            if completed:
                return LoopReply(
                    reply="Parfait ✅ Ton profil est complet. Tu confirmes ces infos ?",
                    next_loop=ctx,
                    updates={"global_state": updated_state},
                    handled=True,
                )
            return LoopReply(
                reply=self._ask_for_slot(self._first_missing_slot(current_fields)),
                next_loop=ctx,
                updates={"global_state": updated_state},
                handled=True,
            )

        if slot_value.confidence == ConfidenceLevel.MEDIUM:
            if should_fallback_to_legacy:
                return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)
            return LoopReply(
                reply=f"Tu peux me donner uniquement {self._slot_label(missing_slot)} ?",
                next_loop=ctx,
                updates={},
                handled=True,
            )

        if should_fallback_to_legacy:
            return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)

        if not any(parsed_item.value for parsed_item in parsed.values()):
            return LoopReply(
                reply=f"On continue d'abord ton profil : {self._ask_for_slot(missing_slot)}",
                next_loop=ctx,
                updates={},
                handled=True,
            )

        return LoopReply(
            reply=f"J'ai besoin d'une réponse claire. Exemple : {self._slot_example(missing_slot)}",
            next_loop=ctx,
            updates={},
            handled=True,
        )

    def _pick_slot_value(self, slot: str, parsed: dict[str, Any], current_fields: dict[str, Any]):
        if slot == "last_name":
            parsed_last_name = parsed.get("last_name")
            if parsed_last_name is not None and parsed_last_name.value:
                return parsed_last_name
            parsed_first_name = parsed.get("first_name")
            if (
                parsed_first_name is not None
                and parsed_first_name.value
                and parsed_first_name.confidence == ConfidenceLevel.HIGH
            ):
                current_first_name = current_fields.get("first_name")
                if isinstance(current_first_name, str) and (
                    parsed_first_name.value.strip().casefold() == current_first_name.strip().casefold()
                ):
                    return None
                return parsed_first_name
            return None
        return parsed.get(slot)

    def _is_safe_start_capture(self, slot: str, parsed: dict[str, Any]) -> bool:
        if slot == "first_name":
            parsed_first_name = parsed.get("first_name")
            parsed_last_name = parsed.get("last_name")
            parsed_birth_date = parsed.get("birth_date")
            return bool(
                parsed_first_name
                and parsed_first_name.confidence == ConfidenceLevel.HIGH
                and parsed_first_name.value
                and not (parsed_last_name and parsed_last_name.value)
                and not (parsed_birth_date and parsed_birth_date.value)
            )
        if slot == "last_name":
            parsed_last_name = parsed.get("last_name")
            parsed_first_name = parsed.get("first_name")
            if parsed_last_name and parsed_last_name.value:
                return parsed_last_name.confidence == ConfidenceLevel.HIGH
            return bool(parsed_first_name and parsed_first_name.confidence == ConfidenceLevel.HIGH and parsed_first_name.value)
        return False

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
        if slot == "first_name":
            return "Tu peux me donner uniquement ton prénom ?"
        if slot == "last_name":
            return "Super. Maintenant, ton nom de famille ?"
        return "Top. Quelle est ta date de naissance au format YYYY-MM-DD ?"

    def _slot_label(self, slot: str) -> str:
        if slot == "first_name":
            return "ton prénom"
        if slot == "last_name":
            return "ton nom de famille"
        return "ta date de naissance"

    def _slot_example(self, slot: str) -> str:
        if slot == "first_name":
            return "Paul"
        if slot == "last_name":
            return "Murt"
        return "1990-01-01"

    def _next_global_state(self, services: Any, *, completed: bool) -> dict[str, Any]:
        global_state = {}
        if isinstance(services, dict):
            global_state = dict(services.get("global_state") or {})
        global_state["mode"] = "onboarding"
        global_state["onboarding_step"] = "profile"
        global_state["onboarding_substep"] = "profile_confirm" if completed else "profile_collect"
        return global_state
