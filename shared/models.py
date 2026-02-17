"""Pydantic contracts shared across backend and agent."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.text_utils import normalize_category_name


class ToolErrorCode(str, Enum):
    """Stable error codes for tool contracts across layers."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    BACKEND_ERROR = "BACKEND_ERROR"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    account_id: str
    category_id: str | None = None
    description: str
    amount: Money
    booked_at: datetime


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    institution: str | None = None


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class ProfileCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    profile_id: UUID
    name: str
    name_norm: str
    exclude_from_totals: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("name_norm")
    @classmethod
    def normalize_name_norm(cls, value: str) -> str:
        return normalize_category_name(value)


class CategoriesListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProfileCategory]


class CategoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    name: str
    exclude_from_totals: bool = False


class CategoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    category_id: UUID
    name: str | None = None
    exclude_from_totals: bool | None = None


class CategoryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    category_id: UUID


ALLOWED_BANK_ACCOUNT_KINDS: frozenset[str] = frozenset({"individual", "joint"})
ALLOWED_BANK_ACCOUNT_ACCOUNT_KINDS: frozenset[str] = frozenset(
    {"personal_current", "personal_savings", "business_current"}
)


class BankAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    profile_id: UUID
    name: str
    kind: str | None = None
    account_kind: str | None = None
    is_system: bool = False


class BankAccountsListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BankAccount]
    default_bank_account_id: UUID | None = None


class BankAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    name: str
    kind: str | None = None
    account_kind: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be empty")
        return normalized

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in ALLOWED_BANK_ACCOUNT_KINDS:
            raise ValueError("Invalid kind")
        return value

    @field_validator("account_kind")
    @classmethod
    def validate_account_kind(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in ALLOWED_BANK_ACCOUNT_ACCOUNT_KINDS:
            raise ValueError("Invalid account_kind")
        return value


class BankAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    bank_account_id: UUID
    set: dict[str, str]

    @field_validator("set")
    @classmethod
    def validate_set(cls, value: dict[str, str]) -> dict[str, str]:
        allowed_fields = {"name", "kind", "account_kind"}
        if not value:
            raise ValueError("set must not be empty")
        invalid_fields = sorted(set(value) - allowed_fields)
        if invalid_fields:
            raise ValueError(f"Unsupported fields: {', '.join(invalid_fields)}")

        normalized_set = dict(value)
        if "name" in normalized_set:
            normalized_name = normalized_set["name"].strip()
            if not normalized_name:
                raise ValueError("name must not be empty")
            normalized_set["name"] = normalized_name
        if "kind" in normalized_set and normalized_set["kind"] not in ALLOWED_BANK_ACCOUNT_KINDS:
            raise ValueError("Invalid kind")
        if (
            "account_kind" in normalized_set
            and normalized_set["account_kind"] not in ALLOWED_BANK_ACCOUNT_ACCOUNT_KINDS
        ):
            raise ValueError("Invalid account_kind")
        return normalized_set


class BankAccountDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    bank_account_id: UUID


class BankAccountSetDefaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    bank_account_id: UUID


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str
    details: dict[str, object] | None = None


class RelevesDirection(str, Enum):
    """Direction selector for releves bank transactions."""

    ALL = "ALL"
    DEBIT_ONLY = "DEBIT_ONLY"
    CREDIT_ONLY = "CREDIT_ONLY"


class ReleveBancaire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    profile_id: UUID | None = None
    date: date
    libelle: str | None = None
    montant: Decimal
    devise: str
    categorie: str | None = None
    payee: str | None = None
    merchant_id: UUID | None = None
    bank_account_id: UUID | None = None


class RelevesFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    date_range: DateRange | None = None
    categorie: str | None = None
    merchant: str | None = None
    merchant_id: UUID | None = None
    bank_account_id: UUID | None = None
    direction: RelevesDirection = RelevesDirection.ALL
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class RelevesSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReleveBancaire]
    limit: int
    offset: int
    total: int | None = None


class RelevesSumResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: Decimal
    count: int
    average: Decimal
    currency: str | None = None
    filters: RelevesFilters | None = None


class RelevesGroupBy(str, Enum):
    """Grouping selector for releves aggregations."""

    CATEGORIE = "categorie"
    PAYEE = "payee"
    MONTH = "month"


class RelevesAggregateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    group_by: RelevesGroupBy
    date_range: DateRange | None = None
    categorie: str | None = None
    merchant: str | None = None
    merchant_id: UUID | None = None
    bank_account_id: UUID | None = None
    direction: RelevesDirection = RelevesDirection.ALL


class RelevesAggregateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: Decimal
    count: int


class RelevesAggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: RelevesGroupBy
    groups: dict[str, RelevesAggregateGroup]
    currency: str | None = None
    filters: RelevesAggregateRequest | None = None


class RelevesImportMode(str, Enum):
    ANALYZE = "analyze"
    COMMIT = "commit"


class RelevesImportModifiedAction(str, Enum):
    KEEP = "keep"
    REPLACE = "replace"


class RelevesImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content_base64: str


class RelevesImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    files: list[RelevesImportFile]
    bank_account_id: UUID | None = None
    import_mode: RelevesImportMode = RelevesImportMode.ANALYZE
    modified_action: RelevesImportModifiedAction = RelevesImportModifiedAction.KEEP


class RelevesImportError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    row_index: int | None = None
    message: str


class RelevesImportPreviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    montant: Decimal
    devise: str
    libelle: str | None = None
    payee: str | None = None
    categorie: str | None = None
    bank_account_id: UUID | None = None


class RelevesImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "releves_import_result"
    imported_count: int
    failed_count: int
    duplicates_count: int
    replaced_count: int
    identical_count: int
    modified_count: int
    new_count: int
    requires_confirmation: bool
    errors: list[RelevesImportError]
    preview: list[RelevesImportPreviewItem]


PROFILE_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "first_name",
        "last_name",
        "birth_date",
        "gender",
        "address_line1",
        "address_line2",
        "postal_code",
        "city",
        "canton",
        "country",
        "personal_situation",
        "professional_situation",
        "default_bank_account_id",
        "active_modules",
    }
)
PROFILE_DEFAULT_CORE_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "birth_date",
    "gender",
    "city",
    "country",
)


class ProfileGetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[str] | None = None

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        invalid_fields = sorted({field for field in value if field not in PROFILE_ALLOWED_FIELDS})
        if invalid_fields:
            raise ValueError(f"Unsupported profile fields: {', '.join(invalid_fields)}")
        return value


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set: dict[str, object | None]

    @field_validator("set")
    @classmethod
    def validate_set(cls, value: dict[str, object | None]) -> dict[str, object | None]:
        if not value:
            raise ValueError("set must contain at least one field")

        string_fields = PROFILE_ALLOWED_FIELDS - {"birth_date", "default_bank_account_id", "active_modules"}
        normalized: dict[str, object | None] = {}

        for field_name, field_value in value.items():
            if field_name not in PROFILE_ALLOWED_FIELDS:
                raise ValueError(f"Unsupported profile field: {field_name}")

            if field_value is None:
                normalized[field_name] = None
                continue

            if field_name == "birth_date":
                if isinstance(field_value, date):
                    normalized[field_name] = field_value
                    continue
                if isinstance(field_value, str):
                    normalized[field_name] = date.fromisoformat(field_value)
                    continue
                raise ValueError("birth_date must be an ISO date string")

            if field_name == "default_bank_account_id":
                normalized[field_name] = UUID(str(field_value))
                continue

            if field_name == "active_modules":
                if isinstance(field_value, list) and all(isinstance(item, str) for item in field_value):
                    normalized[field_name] = field_value
                    continue
                raise ValueError("active_modules must be a list of strings")

            if field_name in string_fields and isinstance(field_value, str):
                normalized[field_name] = field_value
                continue

            raise ValueError(f"Invalid type for profile field: {field_name}")

        return normalized


class ProfileDataResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    data: dict[str, object | None]


# Deprecated aliases kept for backwards compatibility.
TransactionSumDirection = RelevesDirection
TransactionFilters = RelevesFilters
TransactionSearchResult = RelevesSearchResult
TransactionSumResult = RelevesSumResult
