"""Repository interfaces and adapters for profile categories CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from backend.db.supabase_client import SupabaseClient
from backend.repositories.category_utils import normalize_category_name
from shared.models import (
    CategoryCreateRequest,
    CategoryDeleteRequest,
    CategoryUpdateRequest,
    ProfileCategory,
)


class CategoriesRepository(Protocol):
    def list_categories(self, profile_id: UUID) -> list[ProfileCategory]:
        """Return profile categories sorted by creation date."""

    def create_category(self, request: CategoryCreateRequest) -> ProfileCategory:
        """Create and return a category for a profile."""

    def update_category(self, request: CategoryUpdateRequest) -> ProfileCategory:
        """Update and return a category for a profile."""

    def delete_category(self, request: CategoryDeleteRequest) -> None:
        """Delete a category for a profile."""


class InMemoryCategoriesRepository:
    """In-memory categories repository used by tests/dev."""

    def __init__(self) -> None:
        self._categories: list[ProfileCategory] = []

    def list_categories(self, profile_id: UUID) -> list[ProfileCategory]:
        return sorted(
            [category for category in self._categories if category.profile_id == profile_id],
            key=lambda category: category.created_at,
        )

    def create_category(self, request: CategoryCreateRequest) -> ProfileCategory:
        now = datetime.now(timezone.utc)
        category = ProfileCategory(
            id=uuid4(),
            profile_id=request.profile_id,
            name=request.name,
            name_norm=normalize_category_name(request.name),
            exclude_from_totals=request.exclude_from_totals,
            created_at=now,
            updated_at=now,
        )
        self._categories.append(category)
        return category

    def update_category(self, request: CategoryUpdateRequest) -> ProfileCategory:
        for index, category in enumerate(self._categories):
            if category.id != request.category_id or category.profile_id != request.profile_id:
                continue

            updated_name = request.name if request.name is not None else category.name
            updated_category = category.model_copy(
                update={
                    "name": updated_name,
                    "name_norm": normalize_category_name(updated_name),
                    "exclude_from_totals": (
                        request.exclude_from_totals
                        if request.exclude_from_totals is not None
                        else category.exclude_from_totals
                    ),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._categories[index] = updated_category
            return updated_category

        raise ValueError("Category not found")

    def delete_category(self, request: CategoryDeleteRequest) -> None:
        kept_categories = [
            category
            for category in self._categories
            if not (category.id == request.category_id and category.profile_id == request.profile_id)
        ]
        if len(kept_categories) == len(self._categories):
            raise ValueError("Category not found")
        self._categories = kept_categories


class SupabaseCategoriesRepository:
    """Supabase-backed categories repository."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    def _request_rows(
        self,
        *,
        method: str,
        query: list[tuple[str, str | int]] | dict[str, str | int],
        body: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        encoded_query = urlencode(query, doseq=True)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        api_key = self._client.settings.service_role_key
        request = Request(
            url=f"{self._client.settings.url}/rest/v1/profile_categories?{encoded_query}",
            data=payload,
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method=method,
        )

        try:
            with urlopen(request) as response:  # noqa: S310 - URL comes from trusted env config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Supabase request failed with status {exc.code}: {body_text}"
            ) from exc

    def _get_category_or_raise(self, *, profile_id: UUID, category_id: UUID) -> ProfileCategory:
        rows, _ = self._client.get_rows(
            table="profile_categories",
            query={
                "profile_id": f"eq.{profile_id}",
                "id": f"eq.{category_id}",
                "select": "id,profile_id,name,name_norm,exclude_from_totals,created_at,updated_at",
                "limit": 1,
            },
            with_count=False,
        )
        if not rows:
            raise ValueError("Category not found")
        return ProfileCategory.model_validate(rows[0])

    def list_categories(self, profile_id: UUID) -> list[ProfileCategory]:
        rows, _ = self._client.get_rows(
            table="profile_categories",
            query=[
                ("profile_id", f"eq.{profile_id}"),
                ("select", "id,profile_id,name,name_norm,exclude_from_totals,created_at,updated_at"),
                ("order", "created_at.asc"),
            ],
            with_count=False,
        )
        return [ProfileCategory.model_validate(row) for row in rows]

    def create_category(self, request: CategoryCreateRequest) -> ProfileCategory:
        rows = self._request_rows(
            method="POST",
            query={"select": "id,profile_id,name,name_norm,exclude_from_totals,created_at,updated_at"},
            body={
                "profile_id": str(request.profile_id),
                "name": request.name,
                "name_norm": normalize_category_name(request.name),
                "exclude_from_totals": request.exclude_from_totals,
            },
        )
        if not rows:
            raise RuntimeError("Supabase did not return created category")
        return ProfileCategory.model_validate(rows[0])

    def update_category(self, request: CategoryUpdateRequest) -> ProfileCategory:
        payload: dict[str, object] = {}
        if request.name is not None:
            payload["name"] = request.name
            payload["name_norm"] = normalize_category_name(request.name)
        if request.exclude_from_totals is not None:
            payload["exclude_from_totals"] = request.exclude_from_totals

        if not payload:
            return self._get_category_or_raise(profile_id=request.profile_id, category_id=request.category_id)

        rows = self._request_rows(
            method="PATCH",
            query={
                "id": f"eq.{request.category_id}",
                "profile_id": f"eq.{request.profile_id}",
                "select": "id,profile_id,name,name_norm,exclude_from_totals,created_at,updated_at",
            },
            body=payload,
        )
        if not rows:
            raise ValueError("Category not found")
        return ProfileCategory.model_validate(rows[0])

    def delete_category(self, request: CategoryDeleteRequest) -> None:
        rows = self._request_rows(
            method="DELETE",
            query={
                "id": f"eq.{request.category_id}",
                "profile_id": f"eq.{request.profile_id}",
                "select": "id",
            },
            body=None,
        )
        if not rows:
            raise ValueError("Category not found")
