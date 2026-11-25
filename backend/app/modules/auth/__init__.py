"""Public authentication integration surface."""

from app.modules.auth.router import current_user, router

__all__ = ["current_user", "router"]
