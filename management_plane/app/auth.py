import logging
import os
from typing import Optional

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class User(BaseModel):
    id: str
    aud: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None


def get_current_user_from_headers(
    x_tenant_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
) -> User:
    """
    FastAPI dependency to extract tenant identity from internal headers.

    Used for requests from guard.fencio.dev which validates JWT client-side
    and passes tenant ID via headers. No authentication required as nginx
    internal routing provides trust boundary.

    Args:
        x_tenant_id: Required tenant ID from validated JWT
        x_user_id: Optional user ID for future multi-user scenarios

    Returns:
        User object with tenant identity

    Raises:
        HTTPException: If X-Tenant-Id header missing
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Tenant-Id header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return User(
        id=x_tenant_id,
        aud="internal-header",
        role="authenticated",
        email=x_user_id,
    )


async def get_current_tenant(
    x_tenant_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
) -> User:
    """
    Unified tenant resolver.

    Priority:
    1. X-Tenant-Id header → return User
    2. All Supabase env vars unset → local dev mode
    3. Raise 401

    Returns:
        User object with tenant identity

    Raises:
        HTTPException: If authentication fails
    """
    # Header-based auth
    if x_tenant_id:
        return get_current_user_from_headers(x_tenant_id, x_user_id)

    # Local development mode: allow unauthenticated access when Supabase is not configured
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
    supabase_service_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url and not supabase_jwt_secret and not supabase_service_key:
        local_tenant_id = os.getenv("TENANT_ID", "local-dev-user").strip() or "local-dev-user"
        logger.warning("Running in LOCAL DEV MODE - authentication bypassed (Supabase not configured)")
        return User(
            id=local_tenant_id,
            aud="local-dev",
            role="authenticated",
            email="dev@localhost",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing X-Tenant-Id header",
        headers={"WWW-Authenticate": "Bearer"},
    )
