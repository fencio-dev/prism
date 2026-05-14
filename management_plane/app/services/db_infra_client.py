from __future__ import annotations

import json
from typing import Any

import httpx

from app.settings import config


class DbInfraClientError(RuntimeError):
    pass


class DbInfraClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "X-DB-Infra-Service": "prism_management",
            "Accept": "application/json",
        }
        with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds) as client:
            response = client.request(
                method,
                path,
                json=payload,
                params=params,
                headers=headers,
            )
        if allow_not_found and response.status_code == 404:
            return {}
        if response.is_error:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise DbInfraClientError(
                f"db_infra {method} {path} failed: {response.status_code} {detail}"
            )
        if not response.content:
            return {}
        return response.json()

    def get_module_enablement(self, module_name: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/api/v1/platform/module-enablement/{module_name}",
        )

    def validate_runtime_credential(self, api_key: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/v1/prism-management/runtime-auth/validate",
            payload={"api_key": api_key},
        )

    def resolve_runtime_agent(
        self,
        *,
        tenant_id: str,
        integration_type: str,
        runtime_instance_id: str | None,
        integration_agent_ref: str | None,
        endpoint_fingerprint: str | None,
        display_name: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/v1/prism-management/runtime-agent/resolve",
            payload={
                "tenant_id": tenant_id,
                "integration_type": integration_type,
                "runtime_instance_id": runtime_instance_id,
                "integration_agent_ref": integration_agent_ref,
                "endpoint_fingerprint": endpoint_fingerprint,
                "display_name": display_name,
                "metadata": metadata,
            },
        )


db_infra_client = DbInfraClient(
    config.DB_INFRA_BASE_URL,
    config.DB_INFRA_TIMEOUT_SECONDS,
)
