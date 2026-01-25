"""
Unit tests for semantic encoder classes.

Tests:
- SemanticEncoder base class functionality
- IntentEncoder 128-dimensional encoding
- PolicyEncoder RuleVector encoding
- Caching behavior
- Vector normalization
"""

import numpy as np
import pytest

from app.services.semantic_encoder import SemanticEncoder
from app.services.intent_encoder import IntentEncoder
from app.services.policy_encoder import PolicyEncoder, RuleVector
from app.models import (
    IntentEvent, Actor, Resource, Data, Risk,
    DesignBoundary, ActionConstraint, ResourceConstraint,
    DataConstraint, RiskConstraint,
)


# Fixtures


@pytest.fixture
def semantic_encoder():
    """Create SemanticEncoder instance."""
    return SemanticEncoder()


@pytest.fixture
def intent_encoder():
    """Create IntentEncoder instance."""
    return IntentEncoder()


@pytest.fixture
def policy_encoder():
    """Create PolicyEncoder instance."""
    return PolicyEncoder()


@pytest.fixture
def canonical_intent():
    """Create a canonical IntentEvent."""
    return IntentEvent(
        action="read",  # Canonical
        actor=Actor(id="agent-123", type="agent"),
        resource=Resource(type="database", name="users_db"),  # Canonical
        data=Data(sensitivity=["internal"], pii=False, volume="single"),  # Canonical
        risk=Risk(authn="required"),
    )


@pytest.fixture
def canonical_boundary():
    """Create a canonical DesignBoundary."""
    return DesignBoundary(
        id="boundary-456",
        name="test-policy",
        constraints={
            "action": ActionConstraint(
                actions=["read", "write"],  # Canonical
                actor_types=["user", "agent"],
            ),
            "resource": ResourceConstraint(
                types=["database", "api"],  # Canonical
                locations=["cloud"],
            ),
            "data": DataConstraint(
                sensitivity=["internal", "public"],  # Canonical
                pii=False,
            ),
            "risk": RiskConstraint(authn="required"),
        },
    )


# Tests - SemanticEncoder


class TestSemanticEncoder:
    """Test SemanticEncoder base class."""

    def test_encoder_initializes(self, semantic_encoder):
        """Test encoder initialization."""
        assert semantic_encoder is not None

    def test_get_encoder_model(self, semantic_encoder):
        """Test lazy loading of sentence-transformers model."""
        model = semantic_encoder.get_encoder_model()
        assert model is not None

    def test_create_projection_matrix(self, semantic_encoder):
        """Test sparse projection matrix creation."""
        matrix = semantic_encoder.create_sparse_projection_matrix(
            input_dim=384,
            output_dim=32,
            seed=42,
        )

        assert matrix.shape == (32, 384)
        assert matrix.dtype == np.float32

        # Check sparsity (should be ~66%)
        sparsity = np.sum(matrix == 0) / (32 * 384)
        assert 0.6 < sparsity < 0.7

    def test_get_projection_matrix_caching(self, semantic_encoder):
        """Test that projection matrices are cached."""
        matrix1 = semantic_encoder.get_projection_matrix("action", seed=42)
        matrix2 = semantic_encoder.get_projection_matrix("action", seed=42)

        # Should return same object from cache
        assert np.array_equal(matrix1, matrix2)

    def test_encode_text_cached(self, semantic_encoder):
        """Test text encoding with caching."""
        text = "action is read | actor_type is agent"

        embedding1 = semantic_encoder.encode_text_cached(text)
        embedding2 = semantic_encoder.encode_text_cached(text)

        assert embedding1.shape == (384,)
        assert embedding1.dtype == np.float32
        assert np.array_equal(embedding1, embedding2)

    def test_project_and_normalize(self, semantic_encoder):
        """Test projection and normalization."""
        embedding = np.random.randn(384).astype(np.float32)

        projected = semantic_encoder.project_and_normalize(
            embedding_384=embedding,
            slot_name="action",
        )

        assert projected.shape == (32,)
        # Should be unit norm
        norm = np.linalg.norm(projected)
        assert np.isclose(norm, 1.0, atol=1e-6)

    def test_encode_slot(self, semantic_encoder):
        """Test encoding of a single slot."""
        text = "resource_type is database | resource_location is cloud"

        vector = semantic_encoder.encode_slot(text, "resource")

        assert vector.shape == (32,)
        assert vector.dtype == np.float32
        # Should be unit norm
        norm = np.linalg.norm(vector)
        assert np.isclose(norm, 1.0, atol=1e-6)

    def test_cache_stats(self, semantic_encoder):
        """Test cache statistics."""
        # Clear cache
        semantic_encoder.clear_cache()

        # Encode some text
        semantic_encoder.encode_text_cached("test text 1")
        semantic_encoder.encode_text_cached("test text 2")
        semantic_encoder.encode_text_cached("test text 1")  # Cache hit

        stats = semantic_encoder.get_cache_stats()

        assert stats["hits"] >= 0
        assert stats["misses"] >= 0
        assert stats["size"] <= stats["maxsize"]


# Tests - IntentEncoder


class TestIntentEncoder:
    """Test IntentEncoder for 128-dimensional vectors."""

    def test_encoder_initializes(self, intent_encoder):
        """Test intent encoder initialization."""
        assert intent_encoder is not None

    def test_encode_intent_to_128d(self, intent_encoder, canonical_intent):
        """Test encoding of canonical IntentEvent to 128-dim vector."""
        vector = intent_encoder.encode(canonical_intent)

        assert vector.shape == (128,)
        assert vector.dtype == np.float32

    def test_vector_is_normalized(self, intent_encoder, canonical_intent):
        """Test that per-slot normalization is applied."""
        vector = intent_encoder.encode(canonical_intent)

        # Split into 4 slots of 32 dims each
        action_slot = vector[0:32]
        resource_slot = vector[32:64]
        data_slot = vector[64:96]
        risk_slot = vector[96:128]

        # Each slot should be unit-normalized
        for slot in [action_slot, resource_slot, data_slot, risk_slot]:
            norm = np.linalg.norm(slot)
            assert np.isclose(norm, 1.0, atol=1e-6), f"Slot norm is {norm}, expected 1.0"

    def test_encode_deterministic(self, intent_encoder, canonical_intent):
        """Test that encoding is deterministic."""
        vector1 = intent_encoder.encode(canonical_intent)
        vector2 = intent_encoder.encode(canonical_intent)

        assert np.array_equal(vector1, vector2)

    def test_different_intents_different_vectors(self, intent_encoder, canonical_intent):
        """Test that different intents produce different vectors."""
        intent2 = canonical_intent.model_copy(deep=True)
        intent2.action = "write"  # Different action

        vector1 = intent_encoder.encode(canonical_intent)
        vector2 = intent_encoder.encode(intent2)

        assert not np.array_equal(vector1, vector2)

    def test_encode_with_tool_call(self, intent_encoder, canonical_intent):
        """Test encoding with tool call information."""
        intent_with_tool = canonical_intent.model_copy(deep=True)
        intent_with_tool.tool_name = "search_database"
        intent_with_tool.tool_method = "query"

        vector = intent_encoder.encode(intent_with_tool)

        assert vector.shape == (128,)
        assert np.isfinite(vector).all()


# Tests - PolicyEncoder


class TestPolicyEncoder:
    """Test PolicyEncoder for RuleVector encoding."""

    def test_encoder_initializes(self, policy_encoder):
        """Test policy encoder initialization."""
        assert policy_encoder is not None

    def test_encode_boundary_to_rule_vector(self, policy_encoder, canonical_boundary):
        """Test encoding of boundary to RuleVector."""
        rule_vector = policy_encoder.encode(canonical_boundary)

        assert isinstance(rule_vector, RuleVector)
        assert rule_vector.layers["action"].shape == (16, 32)
        assert rule_vector.layers["resource"].shape == (16, 32)
        assert rule_vector.layers["data"].shape == (16, 32)
        assert rule_vector.layers["risk"].shape == (16, 32)

    def test_rule_vector_to_numpy(self, policy_encoder, canonical_boundary):
        """Test flattening RuleVector to numpy array."""
        rule_vector = policy_encoder.encode(canonical_boundary)
        array = rule_vector.to_numpy()

        assert array.shape == (2048,)  # 4 × 16 × 32
        assert array.dtype == np.float32

    def test_anchor_vectors_normalized(self, policy_encoder, canonical_boundary):
        """Test that anchor vectors are per-slot normalized."""
        rule_vector = policy_encoder.encode(canonical_boundary)

        for layer_name, layer_array in rule_vector.layers.items():
            # Check each anchor vector (non-zero ones)
            for i in range(rule_vector.anchor_counts[layer_name]):
                anchor = layer_array[i]
                norm = np.linalg.norm(anchor)
                assert np.isclose(norm, 1.0, atol=1e-6), f"{layer_name} anchor {i} norm is {norm}"

    def test_anchor_counts_tracked(self, policy_encoder, canonical_boundary):
        """Test that actual anchor counts are tracked."""
        rule_vector = policy_encoder.encode(canonical_boundary)

        # Should have at least one anchor per layer
        assert rule_vector.anchor_counts["action"] > 0
        assert rule_vector.anchor_counts["resource"] > 0
        assert rule_vector.anchor_counts["data"] > 0
        assert rule_vector.anchor_counts["risk"] > 0

    def test_encoding_deterministic(self, policy_encoder, canonical_boundary):
        """Test that encoding is deterministic."""
        vector1 = policy_encoder.encode(canonical_boundary).to_numpy()
        vector2 = policy_encoder.encode(canonical_boundary).to_numpy()

        assert np.array_equal(vector1, vector2)

    def test_different_boundaries_different_vectors(self, policy_encoder, canonical_boundary):
        """Test that different boundaries produce different vectors."""
        boundary2 = canonical_boundary.model_copy(deep=True)
        boundary2.constraints.action.actions = ["read"]  # Different actions

        vector1 = policy_encoder.encode(canonical_boundary).to_numpy()
        vector2 = policy_encoder.encode(boundary2).to_numpy()

        assert not np.array_equal(vector1, vector2)

    def test_rule_vector_to_dict(self, policy_encoder, canonical_boundary):
        """Test conversion to dictionary format."""
        rule_vector = policy_encoder.encode(canonical_boundary)
        vector_dict = rule_vector.to_dict()

        assert "layers" in vector_dict
        assert "anchor_counts" in vector_dict
        assert all(name in vector_dict["layers"] for name in ["action", "resource", "data", "risk"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
