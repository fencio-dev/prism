"""
Network policy evaluation service.

Provides deterministic pattern matching against network policy whitelists.
Evaluated BEFORE semantic policies for fast, fail-closed enforcement.
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel

from app.models import NetworkContext, NetworkEndpointRule, NetworkPolicy
from app.services import network_policies as network_policy_service

logger = logging.getLogger(__name__)


class NetworkPolicyResult(BaseModel):
    """
    Result of network policy evaluation.

    Attributes:
        decision: ALLOW or DENY
        policy_id: ID of the policy that matched (if any)
        policy_name: Name of the policy that matched (if any)
        matched_rule: The specific endpoint rule that matched (if any)
        mode: Policy mode (Monitor or Enforce)
        reason: Human-readable explanation of the decision
    """
    decision: str  # "ALLOW" or "DENY"
    policy_id: Optional[str] = None
    policy_name: Optional[str] = None
    matched_rule: Optional[dict] = None
    mode: str = "Enforce"  # "Monitor" or "Enforce"
    reason: str = ""


def evaluate_network_policies(
    tenant_id: str,
    agent_id: str,
    network_ctx: NetworkContext
) -> NetworkPolicyResult:
    """
    Evaluate network policies for the given agent and network context.

    Algorithm:
    1. Fetch all active network policies for agent_id
    2. For each policy, check if any whitelist rule matches the request
    3. If ANY rule matches → ALLOW
    4. If NO rules match → DENY (fail-closed)
    5. Respect policy mode: Monitor logs only, Enforce blocks

    Args:
        tenant_id: Tenant identifier
        agent_id: Agent identifier
        network_ctx: Network request context (protocol, method, URL)

    Returns:
        NetworkPolicyResult with decision and details
    """
    logger.info(
        f"Evaluating network policies for agent {agent_id}: "
        f"{network_ctx.method} {network_ctx.protocol}://{network_ctx.url}"
    )

    # Fetch all active network policies for this agent
    try:
        policies = network_policy_service.list_network_policies(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status="active",
        )
    except Exception as e:
        logger.error(f"Failed to fetch network policies: {e}", exc_info=True)
        # On error, fail open with warning
        return NetworkPolicyResult(
            decision="ALLOW",
            reason=f"Network policy fetch failed: {str(e)}",
        )

    if not policies:
        # No network policies defined = implicit allow
        logger.debug(
            f"No network policies defined for agent {agent_id}, allowing"
        )
        return NetworkPolicyResult(
            decision="ALLOW",
            reason="No network policies defined (implicit allow)",
        )

    logger.info(
        f"Found {len(policies)} active network policies for agent {agent_id}"
    )

    # Check each policy's whitelist
    for policy in policies:
        logger.debug(
            f"Checking policy {policy.name} ({policy.policy_id}) "
            f"with {len(policy.whitelist)} rules"
        )

        for rule in policy.whitelist:
            if matches_endpoint_rule(rule, network_ctx):
                # Rule matched - allow
                logger.info(
                    f"Network policy ALLOW: {policy.name} matched "
                    f"{network_ctx.method} {network_ctx.url}"
                )

                return NetworkPolicyResult(
                    decision="ALLOW",
                    policy_id=policy.policy_id,
                    policy_name=policy.name,
                    matched_rule={
                        "protocol": rule.protocol,
                        "method": rule.method,
                        "url": rule.url,
                    },
                    mode=policy.mode,
                    reason=f"Matched whitelist rule: {rule.method} {rule.url}",
                )

    # No rules matched - fail closed
    # But respect mode: Monitor vs Enforce
    for policy in policies:
        if policy.mode == "Monitor":
            logger.warning(
                f"Network policy MONITOR: Would deny "
                f"{network_ctx.method} {network_ctx.url} "
                f"(not in whitelist, but monitor mode allows)"
            )

            # In monitor mode, log but allow
            return NetworkPolicyResult(
                decision="ALLOW",
                policy_id=policy.policy_id,
                policy_name=policy.name,
                mode="Monitor",
                reason=(
                    f"Not in whitelist but policy in Monitor mode "
                    f"(would DENY in Enforce mode)"
                ),
            )

    # All policies are in Enforce mode and no match found - deny
    logger.warning(
        f"Network policy DENY: {network_ctx.method} {network_ctx.url} "
        f"not in any whitelist (fail-closed)"
    )

    return NetworkPolicyResult(
        decision="DENY",
        policy_id=policies[0].policy_id,
        policy_name=policies[0].name,
        mode="Enforce",
        reason=(
            f"{network_ctx.method} {network_ctx.url} not in whitelist "
            f"(fail-closed enforcement)"
        ),
    )


def matches_endpoint_rule(
    rule: NetworkEndpointRule,
    ctx: NetworkContext
) -> bool:
    """
    Check if network context matches the endpoint rule.

    Supports:
    - Exact match: /api/health
    - Wildcard: /api/users/* matches /api/users/123
    - Multiple wildcards: /api/*/profile matches /api/v1/profile

    Args:
        rule: Network endpoint whitelist rule
        ctx: Network request context

    Returns:
        True if matches, False otherwise
    """
    # Protocol must match
    if rule.protocol != ctx.protocol:
        return False

    # Method must match (case-insensitive)
    if rule.method.upper() != ctx.method.upper():
        return False

    # URL pattern matching
    if rule.url == ctx.url:
        return True  # Exact match

    # Wildcard matching
    if "*" in rule.url:
        # Convert wildcard pattern to regex
        # Escape special regex characters except *
        pattern = re.escape(rule.url).replace(r"\*", ".*")

        # Anchor to start and end
        pattern = f"^{pattern}$"

        if re.match(pattern, ctx.url):
            logger.debug(
                f"Wildcard match: rule {rule.url} matches {ctx.url}"
            )
            return True

    return False
