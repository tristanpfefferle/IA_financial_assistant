from starlette.requests import Request

from backend.auth.supabase_auth import UnauthorizedError, extract_bearer_token


def _build_request(*, authorization: str | None = None, query_string: bytes = b'') -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b'authorization', authorization.encode('latin-1')))
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/token',
        'query_string': query_string,
        'headers': headers,
    }
    return Request(scope)


def test_extract_bearer_token_prefers_authorization_header() -> None:
    request = _build_request(authorization='Bearer header-token', query_string=b'access_token=query-token')
    assert extract_bearer_token(request) == 'header-token'


def test_extract_bearer_token_uses_query_access_token_when_header_missing() -> None:
    request = _build_request(query_string=b'access_token=query-token')
    assert extract_bearer_token(request) == 'query-token'


def test_extract_bearer_token_raises_when_missing() -> None:
    request = _build_request()
    try:
        extract_bearer_token(request)
    except UnauthorizedError as exc:
        assert str(exc) == 'Missing Authorization header'
    else:
        raise AssertionError('Expected UnauthorizedError')
