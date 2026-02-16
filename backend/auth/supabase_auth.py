"""Supabase Auth token validation for API endpoints."""

from __future__ import annotations

import json
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared import config


class UnauthorizedError(Exception):
    """Raised when a bearer token cannot be validated."""


REQUIRED_AUTH_USER_ID_FIELD = "id"


def _is_uuid_like(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def get_user_from_bearer_token(token: str) -> dict[str, object]:
    """Return the Supabase auth user payload for a bearer token."""

    supabase_url = (config.supabase_url() or "").rstrip("/")
    anon_key = config.supabase_anon_key()
    if not supabase_url or not anon_key:
        raise UnauthorizedError("Supabase auth is not configured")

    request = Request(
        url=f"{supabase_url}/auth/v1/user",
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request) as response:  # noqa: S310 - trusted Supabase URL from env
            if response.status != 200:
                raise UnauthorizedError("Unauthorized")
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise UnauthorizedError("Unauthorized")
            user_id = payload.get(REQUIRED_AUTH_USER_ID_FIELD)
            if not isinstance(user_id, str) or not _is_uuid_like(user_id):
                raise UnauthorizedError("Unauthorized")
            return payload
    except HTTPError as exc:
        raise UnauthorizedError("Unauthorized") from exc
    except URLError as exc:
        raise UnauthorizedError("Unauthorized") from exc
