from agent.main import create_agent_loop
from backend.main import create_backend_services
from shared.models import Money, TransactionFilters


def test_imports_succeed() -> None:
    services = create_backend_services()
    loop = create_agent_loop()

    assert "transaction_service" in services
    assert loop.tool_router.route("ping") == "backend-ok"
    assert Money(amount="1.23", currency="EUR").currency == "EUR"
    assert TransactionFilters(page=1, page_size=10).page_size == 10
