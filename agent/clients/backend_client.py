"""Backend client abstraction for agent.

Design choice:
- In dev monorepo mode, call backend service factories directly (in-process).
- In production, this class can be swapped by an HTTP client with same methods.
"""

from backend.main import create_backend_services


class BackendClient:
    """Minimal local backend client adapter."""

    def __init__(self) -> None:
        self._services = create_backend_services()

    def ping(self) -> str:
        """Connectivity placeholder for tool router wiring."""
        return "backend-ok"
