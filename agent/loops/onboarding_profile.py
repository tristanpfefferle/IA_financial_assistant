"""Smart onboarding loop for profile collection."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from agent.loops.confidence import ConfidenceLevel, parse_profile_collect_message
from agent.loops.types import LoopContext, LoopReply
from agent.onboarding.profile_recap import build_profile_recap_reply


_PROFILE_FIELDS: tuple[str, ...] = ("first_name", "last_name", "birth_date")
_YES_VALUES = {"oui", "ouais", "yes", "y", "ok", "daccord", "d'accord", "je confirme"}
_NO_VALUES = {"non", "no", "nop", "nope", "nan"}

_YEAR_TYPO_MONTH_TEXT_PATTERN = re.compile(
    r"\b(\d{1,2})\s+([A-Za-zÀ-ÖØ-öø-ÿ]+)\s+(\d{5})\b",
    re.IGNORECASE,
)
_YEAR_TYPO_DOT_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{5})\b")
_YEAR_TYPO_ISO_PATTERN = re.compile(r"\b(\d{5})-(\d{2})-(\d{2})\b")
_MONTHS = {
    "janvier": 1,
    "janv": 1,
    "fevrier": 2,
    "février": 2,
    "fev": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}


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
        state_dict = (services or {}).get("state") if isinstance(services, dict) else None
        current_fields = self._current_fields(ctx=ctx, profiles_repository=profiles_repository, profile_id=profile_id)
        missing_slot = self._first_missing_slot(current_fields)

        pending_iso = None
        if isinstance(ctx.data, dict):
            pending_iso = ctx.data.get("profile_birth_date_pending_iso")
        if not isinstance(pending_iso, str) and isinstance(state_dict, dict):
            pending_iso = state_dict.get("profile_birth_date_pending_iso")
        if isinstance(pending_iso, str) and pending_iso.strip() and missing_slot == "birth_date":
            pending_iso = pending_iso.strip()
            lowered = message.strip().lower()
            if lowered in _YES_VALUES:
                if profiles_repository is not None and hasattr(profiles_repository, "update_profile_fields"):
                    try:
                        profiles_repository.update_profile_fields(
                            profile_id=profile_id,
                            user_id=user_id,
                            set_dict={"birth_date": pending_iso},
                        )
                    except TypeError:
                        profiles_repository.update_profile_fields(
                            profile_id=profile_id,
                            set_dict={"birth_date": pending_iso},
                        )
                updated_state = self._next_global_state(services, completed=True)
                updated_state["profile_confirmed"] = False
                profile_fields = self._current_fields(ctx=ctx, profiles_repository=profiles_repository, profile_id=profile_id)
                profile_fields["birth_date"] = pending_iso
                return LoopReply(
                    reply=build_profile_recap_reply(profile_fields),
                    next_loop=None,
                    updates={"global_state": updated_state, "profile_birth_date_pending_iso": None},
                    handled=True,
                )
            if lowered in _NO_VALUES:
                return LoopReply(
                    reply=(
                        "Ok 🙂 Peux-tu me redonner ta date de naissance ? Formats acceptés: "
                        "YYYY-MM-DD (ex 2001-05-10), DD.MM.YYYY (ex 10.05.2001), ou '10 mai 2001'."
                    ),
                    next_loop=ctx,
                    updates={"profile_birth_date_pending_iso": None},
                    handled=True,
                )
            return LoopReply(
                reply="Peux-tu répondre par oui ou non pour confirmer l'année de naissance ?",
                next_loop=ctx,
                updates={},
                handled=True,
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

        parsed = parse_profile_collect_message(message)
        slot_value = self._pick_slot_value(missing_slot, parsed, current_fields)

        if missing_slot == "birth_date":
            typo_iso = detect_year_typo(message)
            if typo_iso is not None:
                updated_state = self._next_global_state(services, completed=False)
                return LoopReply(
                    reply=(
                        "Peux-tu confirmer ton année de naissance ? "
                        f"J'ai compris {typo_iso[:4]} (soit {typo_iso})."
                    ),
                    next_loop=ctx,
                    updates={"global_state": updated_state, "profile_birth_date_pending_iso": typo_iso},
                    handled=True,
                )

        if slot_value is None:
            if missing_slot == "birth_date":
                return LoopReply(reply=self._ask_birth_date_with_formats(), next_loop=ctx, updates={}, handled=True)
            return LoopReply(reply=self._ask_for_slot(missing_slot), next_loop=ctx, updates={}, handled=True)

        should_fallback_to_legacy = ctx.step == "start" and missing_slot == "first_name"
        if should_fallback_to_legacy and not self._is_safe_start_capture(missing_slot, parsed):
            return LoopReply(reply="", next_loop=ctx, updates={}, handled=False)

        if slot_value.confidence == ConfidenceLevel.HIGH and slot_value.value:
            if missing_slot == "birth_date" and not is_plausible_birth_date(str(slot_value.value)):
                return LoopReply(
                    reply=(
                        "Ça me paraît improbable 🙂 Peux-tu confirmer ta date de naissance ? Formats acceptés: "
                        "YYYY-MM-DD (ex 2001-05-10), DD.MM.YYYY (ex 10.05.2001), ou '10 mai 2001'."
                    ),
                    next_loop=ctx,
                    updates={"global_state": self._next_global_state(services, completed=False)},
                    handled=True,
                )
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
                try:
                    profile_fields = (
                        profiles_repository.get_profile_fields(profile_id=profile_id, fields=list(_PROFILE_FIELDS))
                        if profiles_repository is not None and hasattr(profiles_repository, "get_profile_fields")
                        else current_fields
                    )
                except Exception:
                    profile_fields = current_fields
                updated_state["profile_confirmed"] = False
                return LoopReply(
                    reply=build_profile_recap_reply(profile_fields),
                    next_loop=None,
                    updates={"global_state": updated_state, "profile_birth_date_pending_iso": None},
                    handled=True,
                )
            return LoopReply(
                reply=self._ask_for_slot(self._first_missing_slot(current_fields)),
                next_loop=ctx,
                updates={"global_state": updated_state},
                handled=True,
            )

        if missing_slot == "birth_date" and not slot_value.value:
            return LoopReply(reply=self._ask_birth_date_with_formats(), next_loop=ctx, updates={}, handled=True)

        if slot_value.confidence == ConfidenceLevel.MEDIUM:
            if missing_slot == "birth_date":
                return LoopReply(
                    reply=self._ask_birth_date_with_formats(),
                    next_loop=ctx,
                    updates={},
                    handled=True,
                )
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
            if missing_slot == "birth_date":
                return LoopReply(reply=self._ask_birth_date_with_formats(), next_loop=ctx, updates={}, handled=True)
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
        return self._ask_birth_date_with_formats()

    def _ask_birth_date_with_formats(self) -> str:
        return (
            "Quelle est ta date de naissance ? Formats acceptés: "
            "YYYY-MM-DD (ex 2001-05-10), DD.MM.YYYY (ex 10.05.2001), ou '10 mai 2001'."
        )

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


def is_plausible_birth_date(iso: str) -> bool:
    """Return whether a birth date is plausible for onboarding."""

    try:
        parsed = date.fromisoformat(iso)
    except ValueError:
        return False
    today = date.today()
    if parsed > today:
        return False
    return (today.year - parsed.year) <= 110


def detect_year_typo(message: str) -> str | None:
    """Detect 5-digit year typo and return plausible corrected ISO date."""

    def _fix_year(year5: str) -> int | None:
        if year5.count("0") == 0:
            return None
        year4 = year5.replace("0", "", 1)
        if len(year4) != 4 or not year4.isdigit():
            return None
        year = int(year4)
        if is_plausible_birth_date(f"{year:04d}-01-01"):
            return year
        return None

    month_text = _YEAR_TYPO_MONTH_TEXT_PATTERN.search(message)
    if month_text is not None:
        day = int(month_text.group(1))
        month = _MONTHS.get(month_text.group(2).lower())
        year = _fix_year(month_text.group(3))
        if month is not None and year is not None:
            try:
                iso = date(year, month, day).isoformat()
            except ValueError:
                iso = None
            if isinstance(iso, str) and is_plausible_birth_date(iso):
                return iso

    dot_match = _YEAR_TYPO_DOT_PATTERN.search(message)
    if dot_match is not None:
        day = int(dot_match.group(1))
        month = int(dot_match.group(2))
        year = _fix_year(dot_match.group(3))
        if year is not None:
            try:
                iso = date(year, month, day).isoformat()
            except ValueError:
                iso = None
            if isinstance(iso, str) and is_plausible_birth_date(iso):
                return iso

    iso_match = _YEAR_TYPO_ISO_PATTERN.search(message)
    if iso_match is not None:
        year = _fix_year(iso_match.group(1))
        month = int(iso_match.group(2))
        day = int(iso_match.group(3))
        if year is not None:
            try:
                iso = date(year, month, day).isoformat()
            except ValueError:
                iso = None
            if isinstance(iso, str) and is_plausible_birth_date(iso):
                return iso

    return None
