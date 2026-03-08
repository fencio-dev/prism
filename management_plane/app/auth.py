import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, Any, Optional

import httpx
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import BaseModel
from supabase import Client as SupabaseClient, create_client

# --- Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")  # Legacy - fallback if JWKS fails
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ALGORITHMS_JWKS = ["RS256"]  # Modern JWKS-based validation
ALGORITHMS_LEGACY = ["HS256"]  # Legacy JWT secret validation

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

class User(BaseModel):
    id: str
    aud: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None

# --- JWKS (JSON Web Key Set) Caching ---

@lru_cache(maxsize=1)
def get_jwks() -> Dict[str, Any]:
    """
    Fetches and caches the JWKS from Supabase.
    The lru_cache ensures we don't make excessive HTTP requests.
    """
    if not SUPABASE_URL:
        raise ValueError("SUPABASE_URL environment variable not set.")
    
    jwks_url = f"{SUPABASE_URL}/auth/v1/jwks"
    try:
        with httpx.Client() as client:
            response = client.get(jwks_url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Failed to fetch JWKS: {e}") from e

# --- Supabase Service Client ---

@lru_cache(maxsize=1)
def get_supabase_service_client() -> SupabaseClient:
    """
    Returns a cached Supabase client authenticated with the service key.
    Used to validate API keys against api_keys table.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for API key validation.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


async def validate_api_key(api_key: str) -> Optional[User]:
    """
    Validate API key against api_keys table and return the associated user.

    Args:
        api_key: The API key value to validate

    Returns:
        User object if valid and active, None otherwise
    """
    try:
        supabase = get_supabase_service_client()

        # Query api_keys table for matching key_value
        response = supabase.table("api_keys").select("user_id, is_active").eq("key_value", api_key).execute()

        if not response.data or len(response.data) == 0:
            return None

        key_record = response.data[0]

        # Check if key is active
        if not key_record.get("is_active", False):
            return None

        user_id = key_record.get("user_id")
        if not user_id:
            return None

        # Update last_used_at timestamp
        supabase.table("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()
        }).eq("key_value", api_key).execute()

        return User(
            id=user_id,
            aud="api-key",
            role="authenticated",
            email=None
        )
    except Exception as e:
        # Log error but don't expose details to client
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"API key validation failed: {e}")
        return None

# --- Token Verification ---

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    x_service_auth: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None)
) -> User:
    """
    FastAPI dependency to verify the JWT and return the current user.
    Supports two authentication modes:
    1. JWT Bearer token (standard Supabase auth)
    2. Service-to-service auth via X-Service-Auth header (for MCP Gateway)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Service-to-service authentication (MCP Gateway → Management Plane)
    if x_service_auth and x_user_id:
        if not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_SERVICE_KEY environment variable not set.")

        if x_service_auth == SUPABASE_SERVICE_KEY:
            # Gateway has already validated the user, trust the user_id
            return User(id=x_user_id, aud="service", role="authenticated")
        else:
            raise credentials_exception

    # Standard JWT authentication
    if not token:
        raise credentials_exception

    # Try JWKS-based validation first (modern Supabase RS256)
    try:
        jwks = get_jwks()
        payload = jwt.decode(
            token,
            jwks,
            algorithms=ALGORITHMS_JWKS,
            options={"verify_aud": False}  # Audience verification handled manually
        )

        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception

        return User(
            id=user_id,
            aud=payload.get("aud"),
            role=payload.get("role"),
            email=payload.get("email"),
        )

    except (JWTError, RuntimeError) as jwks_error:
        # Fallback to legacy JWT secret validation (HS256) if available
        if SUPABASE_JWT_SECRET:
            try:
                payload = jwt.decode(
                    token,
                    SUPABASE_JWT_SECRET,
                    algorithms=ALGORITHMS_LEGACY,
                    options={"verify_aud": False}
                )

                user_id: str = payload.get("sub")
                if user_id is None:
                    raise credentials_exception

                return User(
                    id=user_id,
                    aud=payload.get("aud"),
                    role=payload.get("role"),
                    email=payload.get("email"),
                )

            except JWTError:
                # Both JWKS and legacy validation failed
                raise credentials_exception
        else:
            # No fallback available, raise original JWKS error
            raise credentials_exception


def get_current_user_from_headers(
    x_tenant_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None)
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
        email=x_user_id  # Store user_id in email field if provided
    )


async def get_current_tenant(
    # Try header-based auth first (guard.fencio.dev)
    x_tenant_id: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
    # Fallback to JWT or API key auth (SDK, direct API calls)
    token: Optional[str] = Depends(oauth2_scheme),
    x_service_auth: Optional[str] = Header(None),
) -> User:
    """
    Unified tenant resolver supporting header-based, API key, and JWT authentication.

    Priority:
    1. X-Tenant-Id header (guard.fencio.dev → MP via nginx)
    2. API key (SDK → MP via Authorization: Bearer <api_key>)
    3. JWT token (legacy, direct API calls)
    4. Local dev mode (when Supabase not configured)

    Returns:
        User object with tenant identity

    Raises:
        HTTPException: If authentication fails
    """
    # Header-based auth (guard.fencio.dev)
    if x_tenant_id:
        return get_current_user_from_headers(x_tenant_id, x_user_id)

    # API key auth (SDK)
    if token:
        # Try API key validation first (SDK calls)
        user = await validate_api_key(token)
        if user:
            return user

        # Fall through to JWT validation if API key validation fails
        # This allows the same endpoint to support both API keys and JWTs

    # Local development mode: allow unauthenticated access when Supabase is not configured
    if not SUPABASE_URL and not SUPABASE_JWT_SECRET and not SUPABASE_SERVICE_KEY:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Running in LOCAL DEV MODE - authentication bypassed (Supabase not configured)")
        return User(
            id="local-dev-user",
            aud="local-dev",
            role="authenticated",
            email="dev@localhost"
        )

    # JWT auth (SDK, legacy)
    return await get_current_user(token, x_service_auth, x_user_id)
