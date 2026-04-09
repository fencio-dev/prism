from __future__ import annotations

import os
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fake_dataplane_client = types.ModuleType("app.services.dataplane_client")
fake_generated_pkg = types.ModuleType("app.generated")
fake_rule_installation_pb2 = types.ModuleType("app.generated.rule_installation_pb2")


class DataPlaneError(Exception):
    pass


class DataPlaneClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def install_policies(self, *args, **kwargs):
        return None

    def remove_policy(self, *args, **kwargs):
        return {"success": True, "rules_removed": 1, "message": "removed"}

    def remove_agent_rules(self, *args, **kwargs):
        return {"success": True, "rules_removed": 0, "message": "removed"}

    def query_telemetry(self, *args, **kwargs):
        return None

    def get_session(self, *args, **kwargs):
        return None

    def enforce(self, *args, **kwargs):
        return None


fake_dataplane_client.DataPlaneClient = DataPlaneClient
fake_dataplane_client.DataPlaneError = DataPlaneError
sys.modules.setdefault("app.services.dataplane_client", fake_dataplane_client)


class AnchorVector:
    def __init__(self, values=None) -> None:
        self.values = list(values or [])


class _StringList:
    def __init__(self) -> None:
        self.values = []


class ParamValue:
    def __init__(self) -> None:
        self.string_value = ""
        self.float_value = 0.0
        self.string_list = _StringList()

    def CopyFrom(self, other) -> None:
        self.string_value = getattr(other, "string_value", "")
        self.float_value = getattr(other, "float_value", 0.0)
        self.string_list.values = list(
            getattr(getattr(other, "string_list", None), "values", [])
        )


class RuleAnchorsPayload:
    def __init__(self) -> None:
        self.action_anchors = []
        self.resource_anchors = []
        self.data_anchors = []
        self.risk_anchors = []
        self.action_count = 0
        self.resource_count = 0
        self.data_count = 0
        self.risk_count = 0


class _ParamMap(dict):
    def __missing__(self, key):
        value = ParamValue()
        self[key] = value
        return value


class RuleInstance:
    def __init__(self, **kwargs) -> None:
        self.params = _ParamMap()
        self.slice_weights = []
        for key, value in kwargs.items():
            setattr(self, key, value)


fake_rule_installation_pb2.AnchorVector = AnchorVector
fake_rule_installation_pb2.ParamValue = ParamValue
fake_rule_installation_pb2.RuleAnchorsPayload = RuleAnchorsPayload
fake_rule_installation_pb2.RuleInstance = RuleInstance
fake_generated_pkg.rule_installation_pb2 = fake_rule_installation_pb2
sys.modules.setdefault("app.generated", fake_generated_pkg)
sys.modules.setdefault(
    "app.generated.rule_installation_pb2",
    fake_rule_installation_pb2,
)

from app.main import app
from tests_support.fake_db_infra_server import FakeDbInfraServer


def _policy_payload(policy_id: str = "policy-1") -> dict:
    return {
        "id": policy_id,
        "name": "Block risky export",
        "tenant_id": "tenant-test",
        "agent_id": "agent-1",
        "status": "active",
        "policy_type": "forbidden",
        "priority": 10,
        "match": {
            "op": "export data",
            "t": "customer records",
            "p": "pii payload",
            "ctx": "external destination",
        },
        "connection_match": None,
        "deterministic_conditions": [],
        "semantic_conditions": [],
        "thresholds": {
            "action": 0.8,
            "resource": 0.8,
            "data": 0.8,
            "risk": 0.8,
        },
        "scoring_mode": "min",
        "weights": None,
        "drift_threshold": None,
        "modification_spec": None,
        "notes": "runtime integration test",
    }


class PrismManagementRuntimeIntegrationTest(unittest.TestCase):
    def test_health_and_policy_crud_flow(self) -> None:
        server = FakeDbInfraServer()
        server.start()

        os.environ["DB_INFRA_BASE_URL"] = server.base_url
        os.environ["DB_INFRA_TIMEOUT_SECONDS"] = "5.0"
        os.environ["SUPABASE_URL"] = ""
        os.environ["SUPABASE_JWT_SECRET"] = ""
        os.environ["SUPABASE_SERVICE_KEY"] = ""

        mock_future = MagicMock()
        mock_future.result.return_value = None

        try:
            with patch("app.endpoints.health.grpc.channel_ready_future", return_value=mock_future), \
                 patch("app.endpoints.health.grpc.insecure_channel"), \
                 patch("app.endpoints.policies_v2._persist_anchor_payload", return_value=object()), \
                 patch("app.endpoints.policies_v2._install_to_dataplane"), \
                 patch("app.endpoints.policies_v2.get_data_plane_client") as get_dp_client, \
                 patch("app.endpoints.policies_v2.delete_policy_payload"):

                get_dp_client.return_value.remove_policy.return_value = {
                    "success": True,
                    "rules_removed": 1,
                    "message": "removed",
                }

                client = TestClient(app)

                health = client.get("/health")
                self.assertEqual(health.status_code, 200)
                health_body = health.json()
                self.assertEqual(health_body["status"], "ok")
                self.assertTrue(health_body["components"]["db_infra"])

                headers = {"X-Tenant-Id": "tenant-test"}

                created = client.post(
                    "/api/v2/policies",
                    json=_policy_payload(),
                    headers=headers,
                )
                self.assertEqual(created.status_code, 201)
                created_body = created.json()
                self.assertEqual(created_body["id"], "policy-1")
                self.assertEqual(created_body["tenant_id"], "tenant-test")

                listed = client.get("/api/v2/policies", headers=headers)
                self.assertEqual(listed.status_code, 200)
                listed_body = listed.json()
                self.assertEqual(len(listed_body["policies"]), 1)
                self.assertEqual(listed_body["policies"][0]["id"], "policy-1")

                fetched = client.get("/api/v2/policies/policy-1", headers=headers)
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["name"], "Block risky export")

                deleted = client.delete("/api/v2/policies/policy-1", headers=headers)
                self.assertEqual(deleted.status_code, 200)
                deleted_body = deleted.json()
                self.assertTrue(deleted_body["success"])
                self.assertEqual(deleted_body["policy_id"], "policy-1")

                relisted = client.get("/api/v2/policies", headers=headers)
                self.assertEqual(relisted.status_code, 200)
                self.assertEqual(relisted.json()["policies"], [])
        finally:
            os.environ.pop("DB_INFRA_BASE_URL", None)
            os.environ.pop("DB_INFRA_TIMEOUT_SECONDS", None)
            server.stop()
