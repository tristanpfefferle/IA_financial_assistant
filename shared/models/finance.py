"""Core shared schemas for financial assistant tool contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class Money(BaseModel):
    """Represents a monetary amount with explicit currency."""

    amount: Decimal = Field(..., description="Signed monetary amount.")
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class DateRange(BaseModel):
    """Inclusive date range used by reporting and filtering operations."""

    start_date: date
    end_date: date


class Account(BaseModel):
    """Financial account metadata."""

    id: str
    provider: str | None = None
    name: str
    iban_masked: str | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class Category(BaseModel):
    """Transaction category descriptor."""

    id: str
    label: str
    parent_id: str | None = None


class Transaction(BaseModel):
    """Normalized transaction model exchanged across services/tools."""

    id: str
    booked_at: datetime
    value_date: date | None = None
    description: str
    amount: Money
    account_id: str
    category_id: str | None = None
    counterparty: str | None = None


class TransactionFilters(BaseModel):
    """Filters accepted by transaction-search tools and APIs."""

    date_range: DateRange | None = None
    account_ids: list[str] = Field(default_factory=list)
    category_ids: list[str] = Field(default_factory=list)
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    query: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class ToolError(BaseModel):
    """Standardized error payload returned by tool endpoints."""

    code: Literal[
        "VALIDATION_ERROR",
        "NOT_FOUND",
        "CONFLICT",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "RATE_LIMITED",
        "BACKEND_UNAVAILABLE",
        "INTERNAL_ERROR",
    ]
    message: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    retryable: bool = False
