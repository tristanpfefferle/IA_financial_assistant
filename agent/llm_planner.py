"""Optional LLM-based planner backed by OpenAI tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, Plan, ToolCallPlan
from shared import config
from shared.models import ToolError, ToolErrorCode, TransactionFilters

_ALLOWED_TOOLS = {"finance.transactions.search", "finance.transactions.sum"}
_FALLBACK_CLARIFICATION = "Pouvez-vous préciser votre demande ?"


class OpenAIChatClient(Protocol):
    """Abstraction over OpenAI chat completion for easy mocking in tests."""

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> dict[str, Any]:
        """Create a chat completion payload."""


@dataclass(slots=True)
class OpenAIChatClientImpl:
    """Concrete OpenAI chat client wrapper."""

    api_key: str
    timeout_s: float | None = 20.0

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> dict[str, Any]:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.timeout_s is not None:
            client_kwargs["timeout"] = self.timeout_s

        client = OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        return response.model_dump(mode="json")


@dataclass(slots=True)
class LLMPlanner:
    """Plan messages with an LLM when deterministic parsing cannot route them."""

    model: str = field(default_factory=config.llm_model)
    strict: bool = field(default_factory=config.llm_strict)
    client: OpenAIChatClient | None = None

    @staticmethod
    def _enabled() -> bool:
        """Return whether the LLM planner feature flag is enabled."""
        return config.llm_enabled()

    @staticmethod
    def _tool_definition() -> list[dict[str, Any]]:
        """Return OpenAI tool definitions based on shared transaction filters."""
        filters_schema = TransactionFilters.model_json_schema()

        return [
            {
                "type": "function",
                "function": {
                    "name": "finance.transactions.search",
                    "description": "Search financial transactions using structured filters.",
                    "parameters": filters_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance.transactions.sum",
                    "description": "Compute total amount and count for transactions matching filters.",
                    "parameters": filters_schema,
                },
            },
        ]

    @staticmethod
    def _messages(message: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Tu planifies un appel d'outil financier. "
                    "Utilise finance.transactions.sum pour total/somme/dépenses/revenus, "
                    "finance.transactions.search pour lister/rechercher des transactions. "
                    "Dates au format YYYY-MM-DD si présentes. "
                    "Direction: DEBIT_ONLY pour dépenses, CREDIT_ONLY pour revenus, sinon ALL."
                ),
            },
            {"role": "user", "content": message},
        ]

    def _build_client(self) -> OpenAIChatClient | ErrorPlan:
        if self.client is not None:
            return self.client

        api_key = config.openai_api_key()
        if not api_key:
            return ErrorPlan(
                reply="La configuration de l'assistant IA est incomplète.",
                tool_error=ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="OPENAI_API_KEY is required when AGENT_LLM_ENABLED is true.",
                    details={"retryable": False},
                ),
            )

        return OpenAIChatClientImpl(api_key=api_key)

    def _parse_response(self, response: dict[str, Any]) -> Plan:
        choices = response.get("choices") or []
        first_choice = choices[0] if choices else {}
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        if not isinstance(message, dict):
            message = {}

        content = message.get("content")
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            question = content if isinstance(content, str) and content.strip() else _FALLBACK_CLARIFICATION
            return ClarificationPlan(question=question)

        tool_call = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
        function_data = tool_call.get("function") if isinstance(tool_call, dict) else {}
        if not isinstance(function_data, dict):
            function_data = {}

        tool_name = function_data.get("name")
        if tool_name not in _ALLOWED_TOOLS:
            return ClarificationPlan(
                question=(
                    "Je peux: rechercher des transactions ou calculer une somme. "
                    "Pouvez-vous préciser votre demande ?"
                )
            )

        raw_arguments = function_data.get("arguments")
        if raw_arguments is None:
            raw_arguments = "{}"

        if not isinstance(raw_arguments, str):
            return ErrorPlan(
                reply="Je n'ai pas pu interpréter les paramètres demandés.",
                tool_error=ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Tool arguments must be a JSON string.",
                    details={"raw_arguments": raw_arguments},
                ),
            )

        try:
            parsed_args = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return ErrorPlan(
                reply="Je n'ai pas pu interpréter les paramètres demandés.",
                tool_error=ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Invalid JSON arguments from LLM tool call.",
                    details={"error": str(exc), "raw_arguments": raw_arguments},
                ),
            )

        if not isinstance(parsed_args, dict):
            return ErrorPlan(
                reply="Je n'ai pas pu interpréter les paramètres demandés.",
                tool_error=ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Tool arguments JSON must deserialize to an object.",
                    details={"raw_arguments": raw_arguments, "parsed_type": type(parsed_args).__name__},
                ),
            )

        if tool_name == "finance.transactions.search":
            return ToolCallPlan(
                tool_name=tool_name,
                payload=parsed_args,
                user_reply="OK, je cherche ces transactions.",
            )

        return ToolCallPlan(
            tool_name=tool_name,
            payload=parsed_args,
            user_reply="OK, je calcule la somme des transactions.",
        )

    def _is_vague_clarification(self, question: str) -> bool:
        normalized = question.strip().lower()
        return normalized in {
            "pouvez-vous préciser votre demande ?",
            "merci de préciser.",
            "pouvez-vous préciser ?",
            "je ne comprends pas.",
        }

    def plan(self, message: str) -> Plan:
        """Return an LLM-generated plan when feature flag is enabled."""
        if not self._enabled():
            return NoopPlan(reply="LLM planner not enabled.")

        client_or_error = self._build_client()
        if isinstance(client_or_error, ErrorPlan):
            return client_or_error

        try:
            response = client_or_error.create_chat_completion(
                model=self.model,
                messages=self._messages(message),
                tools=self._tool_definition(),
                tool_choice="auto",
            )
            plan = self._parse_response(response)
        except Exception as exc:
            return ErrorPlan(
                reply="Je rencontre un problème avec l'assistant IA.",
                tool_error=ToolError(
                    code=ToolErrorCode.BACKEND_ERROR,
                    message="LLM planner request failed.",
                    details={"error": str(exc), "retryable": True},
                ),
            )

        if isinstance(plan, ClarificationPlan) and self.strict and self._is_vague_clarification(plan.question):
            return ClarificationPlan(
                question=(
                    "Je peux rechercher des transactions ou calculer une somme. "
                    "Exemple: 'Combien ai-je dépensé en café du 2025-01-01 au 2025-01-31 ?'"
                )
            )

        return plan
