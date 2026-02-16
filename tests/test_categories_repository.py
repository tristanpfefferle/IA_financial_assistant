"""Unit tests for in-memory categories repository CRUD behavior."""

from __future__ import annotations

from uuid import UUID

from backend.repositories.categories_repository import InMemoryCategoriesRepository
from shared.models import CategoryCreateRequest, CategoryDeleteRequest, CategoryUpdateRequest


def test_create_category_normalizes_name_and_sets_flags() -> None:
    repository = InMemoryCategoriesRepository()
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    created = repository.create_category(
        CategoryCreateRequest(
            profile_id=profile_id,
            name="  Courses   Maison  ",
            exclude_from_totals=True,
        )
    )

    assert created.profile_id == profile_id
    assert created.name == "  Courses   Maison  "
    assert created.name_norm == "courses maison"
    assert created.exclude_from_totals is True


def test_list_categories_filters_by_profile_and_orders_by_created_at() -> None:
    repository = InMemoryCategoriesRepository()
    profile_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    profile_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    first = repository.create_category(CategoryCreateRequest(profile_id=profile_a, name="Z", exclude_from_totals=False))
    repository.create_category(CategoryCreateRequest(profile_id=profile_b, name="Other", exclude_from_totals=False))
    second = repository.create_category(CategoryCreateRequest(profile_id=profile_a, name="A", exclude_from_totals=True))

    listed = repository.list_categories(profile_a)

    assert [category.id for category in listed] == [first.id, second.id]


def test_update_category_updates_name_norm_and_exclude_flag() -> None:
    repository = InMemoryCategoriesRepository()
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    category = repository.create_category(
        CategoryCreateRequest(profile_id=profile_id, name="Transport", exclude_from_totals=False)
    )

    updated = repository.update_category(
        CategoryUpdateRequest(
            profile_id=profile_id,
            category_id=category.id,
            name="  Transport   Pro  ",
            exclude_from_totals=True,
        )
    )

    assert updated.id == category.id
    assert updated.name == "  Transport   Pro  "
    assert updated.name_norm == "transport pro"
    assert updated.exclude_from_totals is True
    assert updated.updated_at >= category.updated_at


def test_delete_category_removes_only_target_category() -> None:
    repository = InMemoryCategoriesRepository()
    profile_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    to_delete = repository.create_category(
        CategoryCreateRequest(profile_id=profile_id, name="Abonnement", exclude_from_totals=False)
    )
    to_keep = repository.create_category(
        CategoryCreateRequest(profile_id=profile_id, name="Logement", exclude_from_totals=False)
    )

    repository.delete_category(
        CategoryDeleteRequest(profile_id=profile_id, category_id=to_delete.id)
    )

    listed = repository.list_categories(profile_id)
    assert [category.id for category in listed] == [to_keep.id]
