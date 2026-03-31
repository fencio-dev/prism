"""
Network policy storage service.

Handles CRUD operations for network policies in SQLite.
Network policies are deterministic API endpoint whitelists evaluated
before semantic policies for performance.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from app.models import NetworkPolicy, NetworkEndpointRule
from app.services.policies import _get_connection

logger = logging.getLogger(__name__)


def create_network_policy(policy: NetworkPolicy) -> NetworkPolicy:
    """
    Store a network policy in the database.

    Args:
        policy: NetworkPolicy object to store

    Returns:
        The stored NetworkPolicy

    Raises:
        sqlite3.IntegrityError: If policy_id already exists for tenant
    """
    logger.info(
        f"Creating network policy: {policy.policy_id} "
        f"for agent {policy.agent_id}"
    )

    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO network_policies (
                tenant_id, policy_id, agent_id, name, status, mode,
                whitelist_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.tenant_id,
                policy.policy_id,
                policy.agent_id,
                policy.name,
                policy.status,
                policy.mode,
                json.dumps([rule.model_dump() for rule in policy.whitelist]),
                policy.created_at,
                policy.updated_at,
            ),
        )
        conn.commit()

    logger.info(f"Successfully created network policy: {policy.policy_id}")
    return policy


def get_network_policy(
    tenant_id: str,
    policy_id: str
) -> Optional[NetworkPolicy]:
    """
    Retrieve a single network policy by ID.

    Args:
        tenant_id: Tenant identifier
        policy_id: Policy identifier

    Returns:
        NetworkPolicy if found, None otherwise
    """
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM network_policies
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, policy_id),
        ).fetchone()

    if not row:
        return None

    return _row_to_network_policy(row)


def list_network_policies(
    tenant_id: str,
    agent_id: Optional[str] = None,
    status: Optional[str] = None
) -> list[NetworkPolicy]:
    """
    List network policies with optional filtering.

    Args:
        tenant_id: Tenant identifier
        agent_id: Optional filter by agent
        status: Optional filter by status (active/inactive)

    Returns:
        List of NetworkPolicy objects
    """
    query = "SELECT * FROM network_policies WHERE tenant_id = ?"
    params: list = [tenant_id]

    if agent_id:
        query += " AND agent_id = ?"
        params.append(agent_id)

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC"

    with _get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_network_policy(row) for row in rows]


def update_network_policy(policy: NetworkPolicy) -> NetworkPolicy:
    """
    Update an existing network policy.

    Args:
        policy: NetworkPolicy with updated fields

    Returns:
        Updated NetworkPolicy

    Raises:
        ValueError: If policy doesn't exist
    """
    logger.info(f"Updating network policy: {policy.policy_id}")

    with _get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE network_policies
            SET agent_id = ?, name = ?, status = ?, mode = ?,
                whitelist_json = ?, updated_at = ?
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (
                policy.agent_id,
                policy.name,
                policy.status,
                policy.mode,
                json.dumps([rule.model_dump() for rule in policy.whitelist]),
                policy.updated_at,
                policy.tenant_id,
                policy.policy_id,
            ),
        )

        if cursor.rowcount == 0:
            raise ValueError(
                f"Network policy not found: {policy.policy_id}"
            )

        conn.commit()

    logger.info(f"Successfully updated network policy: {policy.policy_id}")
    return policy


def delete_network_policy(tenant_id: str, policy_id: str) -> bool:
    """
    Delete a network policy.

    Args:
        tenant_id: Tenant identifier
        policy_id: Policy identifier

    Returns:
        True if deleted, False if not found
    """
    logger.info(f"Deleting network policy: {policy_id}")

    with _get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM network_policies
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, policy_id),
        )
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted:
        logger.info(f"Successfully deleted network policy: {policy_id}")
    else:
        logger.warning(f"Network policy not found for deletion: {policy_id}")

    return deleted


def _row_to_network_policy(row: sqlite3.Row) -> NetworkPolicy:
    """
    Convert a database row to a NetworkPolicy object.

    Args:
        row: SQLite row from network_policies table

    Returns:
        NetworkPolicy object
    """
    whitelist_data = json.loads(row["whitelist_json"])
    whitelist = [
        NetworkEndpointRule(**rule) for rule in whitelist_data
    ]

    return NetworkPolicy(
        policy_id=row["policy_id"],
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        status=row["status"],
        mode=row["mode"],
        whitelist=whitelist,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
