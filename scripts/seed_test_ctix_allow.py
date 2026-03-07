#!/usr/bin/env python3
"""Seed a single context_allow policy for ctix_websearch tool calls via the management plane API.

Allows: search for CVE/threat intelligence via ctix_websearch
Blocks: everything else (encrypt_file, etc.) via fail-closed default

Usage:
  python scripts/seed_test_ctix_allow.py
  python scripts/seed_test_ctix_allow.py --tenant-id demo-tenant --base-url http://localhost:8001
"""

from __future__ import annotations

import argparse
import sys
import uuid

import httpx

POLICY_NAMESPACE = uuid.UUID("e1e59f9e-a77f-4a96-bb66-1e2f694cb2dc")
SLUG = "allow-ctix-websearch-cve-threat-intel"


def build_policy_id(tenant_id: str, slug: str) -> str:
    return str(uuid.uuid5(POLICY_NAMESPACE, f"{tenant_id}:{slug}"))


def seed(base_url: str, tenant_id: str) -> None:
    policy_id = build_policy_id(tenant_id, SLUG)

    payload = {
        "id": policy_id,
        "name": "Allow ctix_websearch - CVE and Threat Intelligence Search",
        "tenant_id": tenant_id,
        "status": "active",
        "policy_type": "context_allow",
        "priority": 10,
        "match": {
            "op": "search the web for CVE or threat intelligence information",
            "t": "ctix_websearch threat intelligence search tool",
            "p": None,
            "ctx": None,
        },
        "thresholds": {"action": 0.35, "resource": 0.35, "data": 0.35, "risk": 0.35},
        "scoring_mode": "min",
        "weights": None,
        "drift_threshold": None,
        "modification_spec": None,
        "notes": "Allows ctix_websearch tool calls for CVE/threat intel lookups. All other tool calls denied by default.",
    }

    headers = {"X-Tenant-Id": tenant_id, "Content-Type": "application/json"}

    # Try PUT first (update existing), fall back to POST (create)
    put_url = f"{base_url}/api/v2/policies/{policy_id}"
    post_url = f"{base_url}/api/v2/policies"

    with httpx.Client(timeout=30.0) as client:
        resp = client.put(put_url, json=payload, headers=headers)
        if resp.status_code == 404:
            resp = client.post(post_url, json=payload, headers=headers)

    if not resp.is_success:
        print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    result = resp.json()
    verb = "updated" if resp.status_code == 200 else "created"
    print(f"[{verb}] {result['id']} :: {result['name']}")
    print(f"Tenant: {tenant_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="demo-tenant")
    parser.add_argument("--base-url", default="http://localhost:8001")
    args = parser.parse_args()
    seed(args.base_url, args.tenant_id)


if __name__ == "__main__":
    main()
