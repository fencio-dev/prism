"""
Configuration management for Management Plane.

Loads configuration from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


class Config:
    """Application configuration."""

    PROJECT_ROOT: Path = Path(__file__).parent.parent.parent

    MGMT_ENV_PATH: Path = PROJECT_ROOT / "management_plane" / ".env"
    if MGMT_ENV_PATH.exists():
        load_dotenv(MGMT_ENV_PATH)

    DEMO_ENV_PATH: Path = PROJECT_ROOT / "examples" / "langgraph_demo" / ".env"
    if DEMO_ENV_PATH.exists():
        load_dotenv(DEMO_ENV_PATH)

    # API Configuration
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"
    HOST: str = os.getenv("MGMT_PLANE_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PRISM_PORT", "47000"))

    # CORS Configuration
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",  # React dev server
        "http://localhost:5173",  # Vite dev server
        "http://localhost:8080",  # UI container (dev)
        "https://platform.tupl.xyz",  # Legacy production domain (keep for transition)
        "https://guard.fencio.dev",  # Guard Console
        "https://developer.fencio.dev",  # Developer Platform
    ]

    # Logging Configuration
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = os.getenv(
        "LOG_LEVEL", "INFO"
    )  # type: ignore

    # Application Metadata
    APP_NAME: str = "Management Plane"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "LLM Security Policy Enforcement - Management Plane"

    # Encoding Configuration (Week 2)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    EMBEDDING_CACHE_SIZE: int = int(os.getenv("EMBEDDING_CACHE_SIZE", "10000"))

    # Database Configuration (Week 3)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./mgmt_plane.db")

    # Data Plane Configuration
    data_plane_url: str = os.getenv("DATA_PLANE_URL", f"localhost:{os.getenv('DATA_PLANE_PORT', '50051')}")

    # Chroma Configuration
    CHROMA_URL: str = os.getenv("CHROMA_URL", str(PROJECT_ROOT / "data" / "chroma_data"))
    CHROMA_COLLECTION_PREFIX: str = os.getenv("CHROMA_COLLECTION_PREFIX", "rules_")

    SESSION_DB_PATH: str = os.getenv(
        "SESSION_DB_PATH",
        str(PROJECT_ROOT / "data" / "sessions.db"),
    )
    CANONICALIZATION_LOG_RETENTION_DAYS: int = int(os.getenv("CANONICALIZATION_LOG_RETENTION_DAYS", "90"))
    POLICY_AUDIT_LOG_DIR: str = os.getenv(
        "POLICY_AUDIT_LOG_DIR",
        str(PROJECT_ROOT / "data" / "logs"),
    )

    @classmethod
    def validate(cls) -> None:
        """
        Validate configuration at startup.
        """
        pass

# Global config instance
config = Config()
