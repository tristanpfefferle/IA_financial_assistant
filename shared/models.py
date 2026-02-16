"""Pydantic contracts shared across backend and agent."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolErrorCode(str, Enum):
    """Stable error codes for tool contracts across layers."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    BACKEND_ERROR = "BACKEND_ERROR"
    NOT_FOUND = "NOT_FOUND"


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


class RelevesFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    date_range: DateRange | None = None
    categorie: str | None = None
    merchant: str | None = None
    merchant_id: UUID | None = None
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


# Deprecated aliases kept for backwards compatibility.
TransactionSumDirection = RelevesDirection
TransactionFilters = RelevesFilters
TransactionSearchResult = RelevesSearchResult
TransactionSumResult = RelevesSumResult
