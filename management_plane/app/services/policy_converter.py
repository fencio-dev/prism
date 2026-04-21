"""Convert canonical boundaries to protobuf RuleInstances."""

import json
from typing import Iterable

from app.generated.rule_installation_pb2 import (
    AnchorVector,
    ParamValue,
    RuleAnchorsPayload,
    RuleInstance,
)
from app.models import DesignBoundary
from app.services.policy_encoder import RuleVector


class PolicyConverter:
    """Convert canonical DesignBoundary objects into gRPC RuleInstance formats."""

    RULE_TYPE = "design_boundary"

    @staticmethod
    def boundary_to_rule_instance(
        boundary: DesignBoundary,
        rule_vector: RuleVector,
        tenant_id: str,
    ) -> RuleInstance:
        """Build a RuleInstance protobuf message from a canonical boundary."""

        payload = PolicyConverter.rule_vector_to_anchor_payload(rule_vector)
        layer = ""
        if boundary.connection_match is not None:
            layer = boundary.connection_match.destination_layer

        rule_instance = RuleInstance(
            rule_id=boundary.id,
            family_id=PolicyConverter.RULE_TYPE,
            layer=layer,
            agent_id=boundary.agent_id if boundary.agent_id else tenant_id,
            priority=boundary.priority,
            enabled=boundary.status == "active",
            created_at_ms=int(boundary.created_at * 1000),
            anchors=payload,
        )

        for key, value in PolicyConverter._build_params(boundary).items():
            rule_instance.params[key].CopyFrom(value)

        if boundary.scoring_mode == "weighted-avg":
            weights = boundary.weights
            if weights is None:
                raise ValueError("weights are required when scoring_mode is 'weighted-avg'")
            rule_instance.slice_weights.extend([
                weights.action,
                weights.resource,
                weights.data,
                weights.risk,
            ])
        else:
            rule_instance.slice_weights.extend([1.0, 1.0, 1.0, 1.0])

        rule_instance.policy_type = boundary.policy_type
        rule_instance.drift_threshold = boundary.drift_threshold if boundary.drift_threshold is not None else 0.0
        rule_instance.modification_spec = json.dumps(boundary.modification_spec) if boundary.modification_spec else ""

        return rule_instance

    @staticmethod
    def _build_params(boundary: DesignBoundary) -> dict[str, ParamValue]:
        params: dict[str, ParamValue] = {}

        params["rule_type"] = PolicyConverter._string_param(PolicyConverter.RULE_TYPE)
        params["boundary_id"] = PolicyConverter._string_param(boundary.id)
        params["boundary_name"] = PolicyConverter._string_param(boundary.name)
        params["boundary_status"] = PolicyConverter._string_param(boundary.status)
        params["policy_mode"] = PolicyConverter._string_param(boundary.mode)
        params["policy_type"] = PolicyConverter._string_param(boundary.policy_type)
        params["rule_decision"] = PolicyConverter._string_param(boundary.scoring_mode)
        params["priority"] = PolicyConverter._float_param(float(boundary.priority))
        params["thresholds"] = PolicyConverter._json_param(
            boundary.thresholds.model_dump()
        )

        if boundary.weights is not None:
            params["weights"] = PolicyConverter._json_param(
                boundary.weights.model_dump()
            )

        if boundary.match is not None:
            params["match"] = PolicyConverter._json_param(boundary.match.model_dump())

        if boundary.connection_match is not None:
            params["connection_match"] = PolicyConverter._json_param(
                boundary.connection_match.model_dump()
            )

        if boundary.deterministic_conditions:
            params["deterministic_conditions"] = PolicyConverter._json_param(
                [condition.model_dump() for condition in boundary.deterministic_conditions]
            )

        if boundary.semantic_conditions:
            params["semantic_conditions"] = PolicyConverter._json_param(
                [condition.model_dump() for condition in boundary.semantic_conditions]
            )

        if boundary.notes:
            params["notes"] = PolicyConverter._string_param(boundary.notes)

        return params

    @staticmethod
    def _extract_action_anchors(boundary: DesignBoundary) -> list[str]:
        return [boundary.match.op]

    @staticmethod
    def _extract_resource_anchors(boundary: DesignBoundary) -> list[str]:
        return [boundary.match.t]

    @staticmethod
    def _extract_data_anchors(boundary: DesignBoundary) -> list[str]:
        if boundary.match.p:
            return [boundary.match.p]
        return []

    @staticmethod
    def _extract_risk_anchors(boundary: DesignBoundary) -> list[str]:
        if boundary.match.ctx:
            return [boundary.match.ctx]
        return []

    @staticmethod
    def rule_vector_to_anchor_payload(rule_vector: RuleVector) -> RuleAnchorsPayload:
        payload = RuleAnchorsPayload()
        for slot in ["action", "resource", "data", "risk"]:
            anchor_field = f"{slot}_anchors"
            count_field = f"{slot}_count"
            anchors = PolicyConverter._anchor_vectors(rule_vector.layers[slot])
            getattr(payload, anchor_field).extend(anchors)
            setattr(payload, count_field, rule_vector.anchor_counts[slot])
        return payload

    @staticmethod
    def _anchor_vectors(matrix: Iterable[Iterable[float]]) -> list[AnchorVector]:
        vectors: list[AnchorVector] = []
        for row in matrix:
            vector = AnchorVector(values=list(row))
            vectors.append(vector)
        return vectors

    @staticmethod
    def _string_param(value: str) -> ParamValue:
        param = ParamValue()
        param.string_value = value
        return param

    @staticmethod
    def _float_param(value: float) -> ParamValue:
        param = ParamValue()
        param.float_value = value
        return param

    @staticmethod
    def _json_param(value: object) -> ParamValue:
        param = ParamValue()
        param.string_value = json.dumps(value, sort_keys=True, separators=(',', ':'))
        return param

    @staticmethod
    def _string_list_param(values: Iterable[str]) -> ParamValue:
        param = ParamValue()
        param.string_list.values.extend(values)
        return param
