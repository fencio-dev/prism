"""
Rule Installer - Installs DesignBoundary policies into the Data Plane.

Handles:
1. Startup sync of active policies from SQLite + Chroma to the Rust data plane
2. gRPC communication with Data Plane
3. Proto conversion helpers for rule installation
"""

import logging
import sqlite3
import time
from typing import Optional
from .settings import config
from .chroma_client import upsert_rule_payload, fetch_rule_payload

logger = logging.getLogger(__name__)


def sync_active_policies_to_dataplane() -> None:
    """
    Re-install all active policies from SQLite + Chroma into the Rust data plane.

    Called at startup to ensure the data plane HashMap is not stale after a
    management plane restart.  Failures are logged as warnings; they do not
    prevent the app from starting (the data plane may not be available yet).
    """
    import os
    import numpy as np
    from app.models import DesignBoundary
    from app.services.policy_encoder import RuleVector
    from app.services.dataplane_client import DataPlaneClient, DataPlaneError
    from app.chroma_client import fetch_rule_payload as _fetch_payload

    # Resolve SQLite path the same way policies.py does.
    db_url = config.DATABASE_URL
    if not db_url.startswith("sqlite:///"):
        logger.warning("startup sync: unsupported DATABASE_URL scheme, skipping")
        return
    raw_path = db_url[len("sqlite:///"):]
    sqlite_path = raw_path if raw_path.startswith("/") else os.path.abspath(raw_path)

    if not os.path.exists(sqlite_path):
        logger.info("startup sync: no SQLite DB yet, nothing to sync")
        return

    # Fetch all active policy rows across all tenants.
    try:
        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tenant_id, policy_id FROM policies_v2 WHERE status = 'active'"
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("startup sync: failed to query SQLite: %s", exc)
        return

    if not rows:
        logger.info("startup sync: no active policies found, nothing to sync")
        return

    dp_url = config.data_plane_url
    client = DataPlaneClient(url=dp_url, insecure=True)

    synced = 0
    errors = 0
    for row in rows:
        tenant_id = row["tenant_id"]
        policy_id = row["policy_id"]
        try:
            payload = _fetch_payload(tenant_id, policy_id)
            if not payload:
                logger.warning("startup sync: no Chroma payload for %s/%s, skipping", tenant_id, policy_id)
                errors += 1
                continue

            boundary = DesignBoundary.model_validate(payload["boundary"])
            anchors = payload["anchors"]

            # Reconstruct a RuleVector from stored anchor arrays.
            rv = RuleVector()
            for slot in ("action", "resource", "data", "risk"):
                rows_data = anchors.get(f"{slot}_anchors") or []
                if rows_data:
                    rv.layers[slot] = np.array(rows_data, dtype=np.float32)
                rv.anchor_counts[slot] = int(anchors.get(f"{slot}_count", 0))

            client.install_policies([boundary], [rv])
            synced += 1
        except DataPlaneError as exc:
            logger.warning("startup sync: data plane error for %s/%s: %s", tenant_id, policy_id, exc)
            errors += 1
        except Exception as exc:
            logger.warning("startup sync: failed to sync %s/%s: %s", tenant_id, policy_id, exc)
            errors += 1

    logger.info("startup sync: synced %d active policy(s) to data plane (%d error(s))", synced, errors)

# Import gRPC if available
try:
    import grpc
    from app.generated.rule_installation_pb2 import (
        AnchorVector,
        InstallRulesRequest,
        RemoveAgentRulesRequest,
        RuleAnchorsPayload,
        RuleInstance,
        ParamValue,
        StringList,
    )
    from app.generated.rule_installation_pb2_grpc import DataPlaneStub
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False
    logger.warning("gRPC not available - rule installation disabled")


class RuleInstaller:
    """
    Installs DesignBoundary policies into the Rust Data Plane via gRPC.

    Used by startup sync and direct installation flows.
    """

    def __init__(self, data_plane_url: Optional[str] = None):
        """
        Initialize RuleInstaller.

        Args:
            data_plane_url: Data Plane gRPC URL (default: from config or localhost:50051)
        """
        if not GRPC_AVAILABLE:
            raise RuntimeError("gRPC not available - install grpcio and grpcio-tools")

        self.data_plane_url = data_plane_url or config.data_plane_url or "localhost:50051"
        logger.info(f"RuleInstaller initialized: data_plane={self.data_plane_url}")

    def _param_to_proto(self, value) -> ParamValue:
        """Convert Python value to ParamValue proto."""
        if isinstance(value, list):
            # String list
            return ParamValue(string_list=StringList(values=value))
        elif isinstance(value, str):
            return ParamValue(string_value=value)
        elif isinstance(value, bool):
            return ParamValue(bool_value=value)
        elif isinstance(value, int):
            return ParamValue(int_value=value)
        elif isinstance(value, float):
            return ParamValue(float_value=value)
        else:
            raise ValueError(f"Unsupported param type: {type(value)}")

    def _dict_to_proto_rule(self, rule_dict: dict) -> RuleInstance:
        """Convert rule dictionary to proto RuleInstance."""
        # Convert params
        proto_params = {}
        for key, value in rule_dict.get("params", {}).items():
            proto_params[key] = self._param_to_proto(value)

        return RuleInstance(
            rule_id=rule_dict["rule_id"],
            family_id=rule_dict["family_id"],
            layer=rule_dict["layer"],
            agent_id=rule_dict["agent_id"],
            priority=rule_dict.get("priority", 100),
            enabled=rule_dict.get("enabled", True),
            created_at_ms=rule_dict.get("created_at_ms", int(time.time() * 1000)),
            params=proto_params,
        )

    def _anchors_dict_to_proto(self, anchors: dict) -> RuleAnchorsPayload:
        """Convert stored anchor dict to RuleAnchorsPayload proto."""

        def _to_vectors(key: str) -> list[AnchorVector]:
            rows = anchors.get(key) or []
            vectors: list[AnchorVector] = []
            for row in rows:
                vectors.append(AnchorVector(values=[float(v) for v in row]))
            return vectors

        return RuleAnchorsPayload(
            action_anchors=_to_vectors("action_anchors"),
            action_count=int(anchors.get("action_count", 0)),
            resource_anchors=_to_vectors("resource_anchors"),
            resource_count=int(anchors.get("resource_count", 0)),
            data_anchors=_to_vectors("data_anchors"),
            data_count=int(anchors.get("data_count", 0)),
            risk_anchors=_to_vectors("risk_anchors"),
            risk_count=int(anchors.get("risk_count", 0)),
        )

    async def persist_rule_payload(self, tenant_id: str, rule_dict: dict) -> Optional[dict]:
        """Encode anchors for rule_dict and store them in Chroma."""
        try:
            anchors = await build_tool_whitelist_anchors(rule_dict)
            payload = {
                "rule": rule_dict,
                "anchors": anchors,
            }
            metadata = {
                "agent_id": rule_dict.get("agent_id"),
                "family_id": rule_dict.get("family_id"),
                "layer": rule_dict.get("layer"),
            }
            upsert_rule_payload(tenant_id, rule_dict["rule_id"], payload, metadata)
            return payload
        except Exception as exc:
            logger.error(
                "Failed to persist rule payload for %s: %s",
                rule_dict.get("rule_id"),
                exc,
            )
            return None

    def get_stored_rule_payload(self, tenant_id: str, rule_id: str) -> Optional[dict]:
        """Fetch stored rule payload (anchors) from Chroma."""
        try:
            return fetch_rule_payload(tenant_id, rule_id)
        except Exception as exc:
            logger.warning("Failed to fetch rule payload for %s: %s", rule_id, exc)
            return None

