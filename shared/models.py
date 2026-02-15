"""Pydantic contracts shared across backend and agent."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

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


class TransactionSumDirection(str, Enum):
    """Direction selector used by transaction sum tool."""

    ALL = "ALL"
    DEBIT_ONLY = "DEBIT_ONLY"
    CREDIT_ONLY = "CREDIT_ONLY"


class TransactionFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str | None = None
    category_id: str | None = None
    date_range: DateRange | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    search: str | None = None
    direction: TransactionSumDirection | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str
    details: dict[str, object] | None = None


class TransactionSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Transaction]
    limit: int
    offset: int
    total: int | None = None


class TransactionSumResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: Money
    count: int
    limit: int
    offset: int
    filters: TransactionFilters | None = None
