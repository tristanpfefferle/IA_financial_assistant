"""LLM guardian that validates low-confidence deterministic tool plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from shared import config


class OpenAIJudgeClient(Protocol):
    """Abstraction over OpenAI chat completion for judge payloads."""

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> dict[str, Any]:
        """Create a chat completion payload."""


@dataclass(slots=True)
class OpenAIJudgeClientImpl:
    """Concrete OpenAI chat client wrapper for guardian calls."""

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
class LLMJudgeResult:
    """Normalized guardian verdict."""

    verdict: str
    tool_name: str | None = None
    payload: dict[str, object] | None = None
    user_reply: str | None = None
    question: str | None = None
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class LLMJudge:
    """Validate or repair deterministic plans in ambiguous situations."""

    model: str = field(default_factory=config.llm_model)
    client: OpenAIJudgeClient | None = None

    def _client(self) -> OpenAIJudgeClient | None:
        if self.client is not None:
            return self.client
        api_key = config.openai_api_key()
        if not api_key:
            return None
        return OpenAIJudgeClientImpl(api_key=api_key)

    @staticmethod
    def _tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "guardian_verdict",
                    "description": "Approve, repair, or ask a clarification for a deterministic tool plan.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "verdict": {
                                "type": "string",
                                "enum": ["approve", "repair", "clarify"],
                            },
                            "tool_name": {"type": "string"},
                            "payload": {"type": "object"},
                            "user_reply": {"type": "string"},
                            "question": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["verdict"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    @staticmethod
    def _build_messages(
        *,
        user_message: str,
        deterministic_plan: dict[str, object],
        conversation_context: dict[str, object],
        known_categories: list[str] | None,
    ) -> list[dict[str, str]]:
        system_prompt = (
            "Tu es un validateur de plan d'outils financier. "
            "Compare le message utilisateur, le contexte et le plan déterministe. "
            "Par défaut, approuve le plan. "
            "Utilise repair seulement si une correction est nécessaire. "
            "Utilise clarify si la demande reste ambiguë. "
            "Ne crée jamais de champs payload inconnus; respecte strictement les contrats d'outils existants."
        )
        user_payload: dict[str, object] = {
            "user_message": user_message,
            "deterministic_plan": deterministic_plan,
            "conversation_context": conversation_context,
        }
        if known_categories:
            user_payload["known_categories"] = known_categories

        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ]

    def judge(
        self,
        *,
        user_message: str,
        deterministic_plan: dict[str, object],
        conversation_context: dict[str, object],
        known_categories: list[str] | None = None,
    ) -> LLMJudgeResult:
        client = self._client()
        if client is None:
            return LLMJudgeResult(
                verdict="approve",
                meta={"reason": "judge_client_unavailable"},
            )

        messages = self._build_messages(
            user_message=user_message,
            deterministic_plan=deterministic_plan,
            conversation_context=conversation_context,
            known_categories=known_categories,
        )
        response = client.create_chat_completion(
            model=self.model,
            messages=messages,
            tools=self._tool_definitions(),
            tool_choice="required",
        )

        usage = response.get("usage") if isinstance(response, dict) else None
        usage_meta: dict[str, object] = usage if isinstance(usage, dict) else {}

        try:
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                return LLMJudgeResult(verdict="approve", meta={"reason": "missing_tool_call", **usage_meta})

            arguments = tool_calls[0]["function"].get("arguments", "{}")
            parsed = json.loads(arguments)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return LLMJudgeResult(verdict="approve", meta={"reason": "invalid_judge_response", **usage_meta})

        verdict = parsed.get("verdict")
        if verdict not in {"approve", "repair", "clarify"}:
            verdict = "approve"

        result_meta: dict[str, object] = {"reason": parsed.get("reason", ""), **usage_meta}
        return LLMJudgeResult(
            verdict=verdict,
            tool_name=parsed.get("tool_name") if isinstance(parsed.get("tool_name"), str) else None,
            payload=parsed.get("payload") if isinstance(parsed.get("payload"), dict) else None,
            user_reply=parsed.get("user_reply") if isinstance(parsed.get("user_reply"), str) else None,
            question=parsed.get("question") if isinstance(parsed.get("question"), str) else None,
            meta=result_meta,
        )
