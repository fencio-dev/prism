"""
Management Plane FastAPI application.

Main entry point for the LLM Security Policy Enforcement Management Plane.
Provides REST API for intent comparison, boundary management, and telemetry.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .settings import config
from .endpoints import enforcement_v2, health, policies_v2, telemetry, network_policies
from .services import session_store
from mcp_server.app import mcp, initialize_tools

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown tasks:
    - Validate configuration
    - Initialize encoder services
    - Setup resources
    - Cleanup on shutdown
    """
    # Startup
    logger.info(f"Starting {config.APP_NAME} v{config.VERSION}")

    try:
        # Validate configuration
        config.validate()
        logger.info("Configuration validated successfully")

        # Set up file logging for Docker/persistent deployments
        import os
        import logging as _logging
        _log_dir = Path(os.environ.get("PRISM_HOME", Path.home() / ".prism")) / "data" / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        _fmt = _logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        _mgmt_handler = _logging.FileHandler(_log_dir / "management-plane.log")
        _mgmt_handler.setFormatter(_fmt)
        _logging.getLogger().addHandler(_mgmt_handler)
        _mcp_handler = _logging.FileHandler(_log_dir / "mcp-server.log")
        _mcp_handler.setFormatter(_fmt)
        _logging.getLogger("mcp_server").addHandler(_mcp_handler)
        logger.info("File logging initialized at %s", _log_dir)

        # Initialize encoder services
        try:
            from .endpoints.enforcement_v2 import (
                get_intent_encoder,
                get_policy_encoder,
            )

            # Load intent encoder
            intent_encoder = get_intent_encoder()
            if intent_encoder:
                logger.info("Intent encoder initialized")
            else:
                logger.warning("Intent encoder not available")

            # Load policy encoder
            policy_encoder = get_policy_encoder()
            if policy_encoder:
                logger.info("Policy encoder initialized")
            else:
                logger.warning("Policy encoder not available")

            # Pre-load embedding model into memory so first policy save is not slow
            from .services.semantic_encoder import SemanticEncoder
            SemanticEncoder.get_encoder_model()
            logger.info("Embedding model pre-loaded")

        except Exception as e:
            logger.warning(f"Encoder services initialization warning: {e}")

    except Exception as e:
        logger.error(f"Startup validation failed: {e}", exc_info=True)
        raise

    logger.info(f"Management Plane ready on {config.HOST}:{config.PORT}")

    # Re-install active policies into the data plane to recover from a stale HashMap.
    try:
        from .rule_installer import sync_active_policies_to_dataplane
        sync_active_policies_to_dataplane()
    except Exception as e:
        logger.warning("startup policy sync failed (data plane may not be ready): %s", e)

    # Start session cleanup background task (runs every 10 minutes)
    async def _session_cleanup_loop() -> None:
        while True:
            await asyncio.sleep(600)
            try:
                deleted = session_store.cleanup_expired()
                if deleted > 0:
                    logger.info("session cleanup: removed %d expired session(s)", deleted)
            except Exception as e:
                logger.error("session cleanup failed: %s", e)

    cleanup_task = asyncio.create_task(_session_cleanup_loop())

    yield

    # Shutdown
    cleanup_task.cancel()
    logger.info("Shutting down Management Plane")


initialize_tools()

# Create FastAPI application
app = FastAPI(
    title=config.APP_NAME,
    description=config.DESCRIPTION,
    version=config.VERSION,
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(health.router)
app.include_router(enforcement_v2.router, prefix=config.API_V2_PREFIX)
app.include_router(policies_v2.router, prefix=config.API_V2_PREFIX)
app.include_router(network_policies.router)  # Network policies (includes /api/v2 in router prefix)
app.include_router(telemetry.router, prefix=config.API_V2_PREFIX)
app.mount("/mcp", mcp.http_app())

_ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
if _ui_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_ui_dist), html=True), name="ui")
    logger.info("UI static files mounted from %s", _ui_dist)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for uncaught errors.

    Args:
        request: The request that caused the error
        exc: The exception that was raised

    Returns:
        JSON response with error details
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "type": "internal_error",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL.lower(),
        reload=False,
    )
