"""Backend entrypoint placeholder."""

from backend.services.transaction_service import TransactionService


def create_backend_services() -> dict[str, object]:
    """Factory for backend service objects used by API or local integrations."""
    return {"transaction_service": TransactionService()}
