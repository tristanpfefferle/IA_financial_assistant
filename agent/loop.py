"""Minimal deterministic agent loop (no LLM calls)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from agent.tool_router import ToolRouter
from shared.models import ToolError, ToolErrorCode


@dataclass(slots=True)
class AgentReply:
    """Serializable chat output for API responses."""

    reply: str
    tool_result: dict[str, object] | None = None


@dataclass(slots=True)
class AgentLoop:
    tool_router: ToolRouter

    _SEARCH_TOKENS = {"from", "to", "account", "category", "limit", "offset", "min", "max"}

    def _parse_search_payload(self, message: str) -> tuple[dict[str, object] | None, ToolError | None]:
        body = message.split(":", maxsplit=1)[1].strip()
        if not body:
            return {"search": None, "limit": 50, "offset": 0}, None

        words = body.split()
        search_parts: list[str] = []
        first_token_index = len(words)

        for index, word in enumerate(words):
            token, _, _ = word.partition(":")
            if token.lower() in self._SEARCH_TOKENS and ":" in word:
                first_token_index = index
                break
            search_parts.append(word)

        payload: dict[str, object] = {
            "search": " ".join(search_parts).strip() or None,
            "limit": 50,
            "offset": 0,
        }
        token_values: dict[str, str] = {}
        for raw in words[first_token_index:]:
            token, sep, value = raw.partition(":")
            if not sep:
                continue
            token_key = token.lower()
            if token_key in self._SEARCH_TOKENS:
                token_values[token_key] = value

        try:
            if "from" in token_values or "to" in token_values:
                if "from" not in token_values or "to" not in token_values:
                    return None, ToolError(
                        code=ToolErrorCode.VALIDATION_ERROR,
                        message="Les dates doivent inclure from:YYYY-MM-DD et to:YYYY-MM-DD.",
                        details={"from": token_values.get("from"), "to": token_values.get("to")},
                    )
                payload["date_range"] = {
                    "start_date": date.fromisoformat(token_values["from"]),
                    "end_date": date.fromisoformat(token_values["to"]),
                }

            if "account" in token_values:
                payload["account_id"] = token_values["account"]

            if "category" in token_values:
                payload["category_id"] = token_values["category"]

            if "limit" in token_values:
                payload["limit"] = int(token_values["limit"])

            if "offset" in token_values:
                payload["offset"] = int(token_values["offset"])

            if "min" in token_values:
                payload["min_amount"] = Decimal(token_values["min"])

            if "max" in token_values:
                payload["max_amount"] = Decimal(token_values["max"])
        except ValueError as exc:
            return None, ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="Format invalide dans la commande search:. Vérifiez les dates et nombres.",
                details={"error": str(exc), "input": token_values},
            )
        except InvalidOperation as exc:
            return None, ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="Montant invalide dans la commande search:. Utilisez un nombre décimal valide.",
                details={"error": str(exc), "input": token_values},
            )

        return payload, None

    def handle_user_message(self, message: str) -> AgentReply:
        normalized_message = message.strip()

        if normalized_message.lower() == "ping":
            return AgentReply(reply="pong")

        if normalized_message.lower().startswith("search:"):
            payload, parse_error = self._parse_search_payload(normalized_message)
            if parse_error is not None:
                return AgentReply(
                    reply="Je n'ai pas pu interpréter la commande search:. Corrigez le format puis réessayez.",
                    tool_result=parse_error.model_dump(mode="json"),
                )

            result = self.tool_router.call(
                "finance.transactions.search",
                payload,
            )
            return AgentReply(
                reply="Voici le résultat de la recherche de transactions.",
                tool_result=result.model_dump(mode="json"),
            )

        return AgentReply(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
