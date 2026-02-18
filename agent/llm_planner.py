"""Optional LLM-based planner backed by OpenAI tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.planner import ClarificationPlan, ErrorPlan, NoopPlan, Plan, ToolCallPlan
from shared import config
from shared.models import (
    BankAccountDeleteRequest,
    CategoryCreateRequest,
    CategoryDeleteRequest,
    CategoryUpdateRequest,
    ProfileGetRequest,
    ProfileUpdateRequest,
    RelevesAggregateRequest,
    RelevesFilters,
    RelevesImportRequest,
    ToolError,
    ToolErrorCode,
)

_ALLOWED_TOOLS = {
    "finance_releves_search",
    "finance_releves_sum",
    "finance_releves_aggregate",
    "finance_releves_import_files",
    "finance_categories_list",
    "finance_categories_create",
    "finance_categories_update",
    "finance_categories_delete",
    "finance_profile_get",
    "finance_profile_update",
    "finance_bank_accounts_list",
    "finance_bank_accounts_delete",
}
_TOOL_ALIASES = {
    "finance_transactions_search": "finance_releves_search",
    "finance_transactions_sum": "finance_releves_sum",
}
_FALLBACK_CLARIFICATION = "Pouvez-vous préciser votre demande ?"
_DELETE_BANK_ACCOUNT_FALLBACK = (
    "La suppression de compte bancaire est bien disponible via l'outil "
    "finance_bank_accounts_delete avec confirmation. Indiquez le nom du compte à supprimer."
)


def _planner_system_prompt() -> str:
    """Return the planner system prompt with explicit routing constraints."""
    return (
        "Tu planifies un appel d'outil financier. "
        "Transactions et relevés désignent la même source de vérité (releves_bancaires). "
        "Utilise toujours finance_releves_search pour lister/rechercher et finance_releves_sum pour total/somme/dépenses/revenus. "
        "Lister les comptes bancaires => finance_bank_accounts_list. "
        "Supprimer un compte => finance_bank_accounts_delete (avec confirmation). "
        "Dates au format YYYY-MM-DD si présentes. "
        "Direction: DEBIT_ONLY pour dépenses, CREDIT_ONLY pour revenus, sinon ALL. "
        "Pour le profil, utilise toujours les champs canoniques: first_name, last_name, birth_date, gender, address_line1, address_line2, postal_code, city, canton, country, personal_situation, professional_situation. "
        "Ne génère jamais 'ville'/'pays' dans le payload: utilise 'city'/'country'. "
        "Règle explicite catégories: si le message contient une intention de suppression (supprime/efface/enlève/delete) + 'catégorie' + un nom, choisis finance_categories_delete avec {'category_name': <nom>}. "
        "Règle explicite profil: 'ville', 'j'habite', ou 'mettre <X> comme ville' cible par défaut le champ canonical 'city' sauf si l'utilisateur demande explicitement une autre clé canonique (ex: address_line2)."
    )


def _planner_few_shots() -> list[dict[str, str]]:
    """Return few-shot examples anchoring planner decisions."""
    return [
        {
            "role": "system",
            "content": (
                "Exemple 1\n"
                "User: Supprime la catégorie Restaurants\n"
                "Tool: finance_categories_delete\n"
                "Arguments JSON: {\"category_name\":\"Restaurants\"}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Exemple 2\n"
                "User: Peux-tu supprimer ma catégorie restaurants stp ?\n"
                "Tool: finance_categories_delete\n"
                "Arguments JSON: {\"category_name\":\"restaurants\"}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Exemple 3\n"
                "User: Peux-tu mettre Choëx comme ville stp ?\n"
                "Tool: finance_profile_update\n"
                "Arguments JSON: {\"set\":{\"city\":\"Choëx\"}}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Exemple 4\n"
                "User: Mon code postal est 1871\n"
                "Tool: finance_profile_update\n"
                "Arguments JSON: {\"set\":{\"postal_code\":\"1871\"}}"
            ),
        },
    ]


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
    def _schema_without_profile_id(schema: dict[str, Any]) -> dict[str, Any]:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("profile_id", None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [item for item in required if item != "profile_id"]
        return schema

    @staticmethod
    def _tool_definition() -> list[dict[str, Any]]:
        """Return OpenAI tool definitions based on shared schemas."""
        releves_filters_schema = LLMPlanner._schema_without_profile_id(RelevesFilters.model_json_schema())
        releves_aggregate_schema = LLMPlanner._schema_without_profile_id(
            RelevesAggregateRequest.model_json_schema()
        )
        categories_create_schema = LLMPlanner._schema_without_profile_id(
            CategoryCreateRequest.model_json_schema()
        )
        categories_update_schema = LLMPlanner._schema_without_profile_id(
            CategoryUpdateRequest.model_json_schema()
        )
        categories_delete_schema = LLMPlanner._schema_without_profile_id(
            CategoryDeleteRequest.model_json_schema()
        )
        profile_get_schema = ProfileGetRequest.model_json_schema()
        profile_update_schema = ProfileUpdateRequest.model_json_schema()
        bank_account_delete_schema = LLMPlanner._schema_without_profile_id(
            BankAccountDeleteRequest.model_json_schema()
        )
        releves_import_schema = LLMPlanner._schema_without_profile_id(
            RelevesImportRequest.model_json_schema()
        )

        return [
            {
                "type": "function",
                "function": {
                    "name": "finance_releves_search",
                    "description": "Search releves bancaires using structured filters.",
                    "parameters": releves_filters_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_releves_sum",
                    "description": "Compute total amount and count for releves bancaires matching filters.",
                    "parameters": releves_filters_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_releves_aggregate",
                    "description": "Aggregate releves bancaires by group with totals and counts.",
                    "parameters": releves_aggregate_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_releves_import_files",
                    "description": "Import one or more bank statement files in analyze or commit mode.",
                    "parameters": releves_import_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_categories_list",
                    "description": "List profile categories and whether they are excluded from totals.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_categories_create",
                    "description": "Create a new category for the current profile.",
                    "parameters": categories_create_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_categories_update",
                    "description": "Update an existing category for the current profile.",
                    "parameters": categories_update_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_categories_delete",
                    "description": "Delete a category for the current profile.",
                    "parameters": categories_delete_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_bank_accounts_list",
                    "description": "List bank accounts for the current profile.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_bank_accounts_delete",
                    "description": "Delete a bank account for the current profile (requires user confirmation in the flow).",
                    "parameters": bank_account_delete_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_profile_get",
                    "description": "Read selected profile fields from the current user profile.",
                    "parameters": profile_get_schema,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finance_profile_update",
                    "description": "Update selected profile fields for the current user profile.",
                    "parameters": profile_update_schema,
                },
            },
        ]

    @staticmethod
    def _messages(message: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": _planner_system_prompt(),
            },
            *_planner_few_shots(),
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
            normalized_question = question.casefold()
            if (
                "pas d'outil" in normalized_question
                and "compte bancaire" in normalized_question
                and ("supprim" in normalized_question or "delete" in normalized_question)
            ):
                question = _DELETE_BANK_ACCOUNT_FALLBACK
            return ClarificationPlan(question=question)

        tool_call = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
        function_data = tool_call.get("function") if isinstance(tool_call, dict) else {}
        if not isinstance(function_data, dict):
            function_data = {}

        tool_name = function_data.get("name")
        if isinstance(tool_name, str):
            tool_name = _TOOL_ALIASES.get(tool_name, tool_name)

        if tool_name not in _ALLOWED_TOOLS:
            return ErrorPlan(
                reply="Je ne peux pas exécuter cet outil.",
                tool_error=ToolError(
                    code=ToolErrorCode.UNKNOWN_TOOL,
                    message="Unsupported tool requested by LLM planner.",
                    details={"tool_name": tool_name},
                ),
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

        return ToolCallPlan(
            tool_name=tool_name,
            payload=parsed_args,
            user_reply="",
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
                    "Je peux rechercher des relevés ou calculer une somme. "
                    "Exemple: 'Combien ai-je dépensé en café du 2025-01-01 au 2025-01-31 ?'"
                )
            )

        return plan
