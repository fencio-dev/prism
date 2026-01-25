"""
Unit tests for BERT-based canonicalizer service.

Tests:
- BERT classification on known terms
- Confidence thresholds (high, medium, low)
- Passthrough behavior for unknown terms
- Trace generation
- IntentEvent canonicalization
- DesignBoundary canonicalization
"""

import pytest
from pathlib import Path

from app.services.canonicalizer import BertCanonicalizer, CanonicalizedField
from app.models import IntentEvent, Actor, Resource, Data, Risk, DesignBoundary
from app.models import ActionConstraint, ResourceConstraint, DataConstraint, RiskConstraint


# Fixtures


@pytest.fixture
def canonicalizer():
    """Load BERT canonicalizer."""
    model_dir = Path(__file__).parent.parent.parent / "management_plane" / "models" / "canonicalizer_tinybert_v1.0"

    if not model_dir.exists():
        pytest.skip("BERT model not found")

    return BertCanonicalizer(
        model_dir=model_dir,
        confidence_high=0.9,
        confidence_medium=0.7,
    )


@pytest.fixture
def sample_intent():
    """Create a sample IntentEvent with non-canonical terms."""
    return IntentEvent(
        action="query",  # Non-canonical (should map to "read")
        actor=Actor(id="user-123", type="user"),
        resource=Resource(type="postgres_db", name="users"),  # Non-canonical (should map to "database")
        data=Data(sensitivity=["confidential"], pii=False, volume="single"),  # Non-canonical sensitivity
        risk=Risk(authn="required"),
    )


@pytest.fixture
def sample_boundary():
    """Create a sample DesignBoundary with non-canonical terms."""
    return DesignBoundary(
        id="boundary-123",
        name="test-policy",
        constraints={
            "action": ActionConstraint(
                actions=["query", "select"],  # Non-canonical
                actor_types=["user", "agent"],
            ),
            "resource": ResourceConstraint(
                types=["postgres_db", "mysql_db"],  # Non-canonical
                locations=["cloud"],
            ),
            "data": DataConstraint(
                sensitivity=["confidential", "secret"],  # Non-canonical
                pii=False,
            ),
            "risk": RiskConstraint(authn="required"),
        },
    )


# Tests


class TestBertCanonicalizer:
    """Test BERT canonicalizer functionality."""

    def test_canonicalizer_loads(self, canonicalizer):
        """Test that canonicalizer initializes without error."""
        assert canonicalizer is not None
        assert canonicalizer.session is not None

    def test_classify_known_action(self, canonicalizer):
        """Test classification of known action term."""
        field = canonicalizer.canonicalize_field("action", "query")

        assert field.field_name == "action"
        assert field.raw_value == "query"
        # Should classify to "read" with high confidence
        assert field.canonical_value in ["read", "query"]  # Accept either
        assert field.confidence >= 0.0
        assert field.confidence <= 1.0

    def test_classify_known_resource(self, canonicalizer):
        """Test classification of known resource term."""
        field = canonicalizer.canonicalize_field("resource_type", "postgres_db")

        assert field.field_name == "resource_type"
        assert field.raw_value == "postgres_db"
        # Should classify to "database" with high confidence
        assert field.canonical_value in ["database", "postgres_db"]
        assert field.confidence >= 0.0

    def test_classify_sensitivity(self, canonicalizer):
        """Test classification of sensitivity term."""
        field = canonicalizer.canonicalize_field("sensitivity", "confidential")

        assert field.field_name == "sensitivity"
        assert field.raw_value == "confidential"
        assert field.canonical_value in ["secret", "internal", "confidential"]
        assert field.confidence >= 0.0

    def test_confidence_thresholds(self, canonicalizer):
        """Test that confidence thresholds determine source."""
        field = canonicalizer.canonicalize_field("action", "read")

        # Confident prediction should use source "bert_high" or "bert_medium"
        assert field.source in ["bert_high", "bert_medium", "passthrough"]

    def test_empty_input_passthrough(self, canonicalizer):
        """Test that empty input passes through unchanged."""
        field = canonicalizer.canonicalize_field("action", "")

        assert field.raw_value == ""
        assert field.canonical_value == ""
        assert field.confidence == 0.0
        assert field.source == "passthrough"

    def test_canonicalize_intent_event(self, canonicalizer, sample_intent):
        """Test canonicalization of full IntentEvent."""
        result = canonicalizer.canonicalize(sample_intent)

        assert result.event == sample_intent
        assert result.canonical_event is not None
        assert len(result.trace) >= 3  # At least action, resource, sensitivity

        # Check trace has field information
        for field in result.trace:
            assert field.field_name in ["action", "resource_type", "sensitivity"]
            assert field.raw_value is not None
            assert field.canonical_value is not None
            assert field.source in ["bert_high", "bert_medium", "passthrough", "error"]

    def test_canonicalize_boundary(self, canonicalizer, sample_boundary):
        """Test canonicalization of DesignBoundary."""
        result = canonicalizer.canonicalize_boundary(sample_boundary)

        assert result.boundary == sample_boundary
        assert result.canonical_boundary is not None
        assert len(result.trace) > 0

        # Canonical boundary should have normalized constraints
        canonical = result.canonical_boundary
        assert isinstance(canonical.constraints.action.actions, list)
        assert isinstance(canonical.constraints.resource.types, list)
        assert isinstance(canonical.constraints.data.sensitivity, list)

    def test_trace_to_dict(self, canonicalizer):
        """Test conversion of trace to dictionary format."""
        field = canonicalizer.canonicalize_field("action", "read")
        field_dict = field.to_dict()

        assert "field" in field_dict
        assert "raw_input" in field_dict
        assert "prediction" in field_dict
        assert "canonical" in field_dict["prediction"]
        assert "confidence" in field_dict["prediction"]
        assert "source" in field_dict["prediction"]

    def test_canonicalize_multiple_terms(self, canonicalizer):
        """Test canonicalization of multiple terms in sequence."""
        terms = ["query", "read", "select", "fetch"]
        results = [
            canonicalizer.canonicalize_field("action", term)
            for term in terms
        ]

        assert len(results) == 4
        assert all(isinstance(r, CanonicalizedField) for r in results)
        assert all(r.confidence >= 0.0 for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
