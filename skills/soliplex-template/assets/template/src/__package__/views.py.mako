"""A custom FastAPI router for the ``${package_name}`` Soliplex install.

Soliplex registers extra routers by dotted name via the installation-level
``app_router_operations`` (see ``backend/environment/installation.yaml``).
This module exposes ``router``, referenced there as
``${package_name}.views.router``.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/custom/ping")
def ping() -> dict[str, str]:
    """A trivial endpoint contributed by this project's own package."""
    return {"ping": "pong"}
