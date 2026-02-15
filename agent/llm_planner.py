"""Optional LLM-based planner backed by OpenAI tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, Plan, ToolCallPlan
from shared import config
from shared.models import ToolError, ToolErrorCode, TransactionFilters


@dataclass(slots=True)
class LLMPlanner:
    """Plan messages with an LLM when deterministic parsing cannot route them."""

    model: str = field(default_factory=config.llm_model)

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
            }
        ]

    def plan(self, message: str) -> Plan:
        """Return an LLM-generated plan when feature flag is enabled."""
        if not self._enabled():
            return NoopPlan(reply="LLM planner not enabled.")

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

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Tu es un planificateur d'actions. Utilise l'outil "
                            "finance.transactions.search quand la demande concerne une "
                            "recherche de transactions. Sinon demande une clarification."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                tools=self._tool_definition(),
                tool_choice="auto",
            )

            llm_message = response.choices[0].message
            tool_calls = llm_message.tool_calls or []
            if tool_calls:
                tool_call = tool_calls[0]
                if tool_call.function.name == "finance.transactions.search":
                    parsed_args = json.loads(tool_call.function.arguments or "{}")
                    return ToolCallPlan(
                        tool_name="finance.transactions.search",
                        payload=parsed_args,
                        user_reply="OK, je cherche ces transactions.",
                    )

            clarification = llm_message.content or "Pouvez-vous préciser votre demande ?"
            return ClarificationPlan(question=clarification)

        except Exception as exc:
            return ErrorPlan(
                reply="Je rencontre un problème avec l'assistant IA.",
                tool_error=ToolError(
                    code=ToolErrorCode.BACKEND_ERROR,
                    message="LLM planner request failed.",
                    details={"error": str(exc), "retryable": True},
                ),
            )
