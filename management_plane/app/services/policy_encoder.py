"""
Policy Encoder for RuleVector anchor encoding.

Subclass of SemanticEncoder that:
1. Extracts anchors from canonical DesignBoundary for 4 layers
2. Encodes each anchor to 32-dimensional vector
3. Aggregates to 4×16×32 RuleVector structure:
   - 4 layers: action, resource, data, risk
   - 16 anchors per layer (padded with zeros if fewer)
   - 32 dimensions per anchor

The base class handles:
- Model loading (sentence-transformers)
- Embedding generation (384d)
- Projection matrix creation (sparse random projection)
- Caching

This class adds:
- Anchor extraction from DesignBoundary constraints
- Anchor encoding with max/padding logic
- RuleVector aggregation

Example:
    encoder = PolicyEncoder()
    canonical_boundary = DesignBoundary(...)
    rule_vector = encoder.encode(canonical_boundary)  # Returns RuleVector(4, 16, 32)
"""

import logging
from typing import Tuple

import numpy as np

from app.models import DesignBoundary
from app.services.param_canonicalizer import canonicalize_params
from app.services.semantic_encoder import SemanticEncoder

logger = logging.getLogger(__name__)


class RuleVector:
    """
    4×16×32 tensor representation of policy boundaries.

    Structure:
    - 4 layers: action, resource, data, risk
    - Each layer has 16 anchor slots (padded with zeros if fewer anchors)
    - Each anchor is 32-dimensional

    Flattened shape: (4, 16, 32) → can be reshaped to (2048,) for comparison
    """

    def __init__(self):
        """Initialize empty RuleVector."""
        self.layers = {
            "action": np.zeros((16, 32), dtype=np.float32),
            "resource": np.zeros((16, 32), dtype=np.float32),
            "data": np.zeros((16, 32), dtype=np.float32),
            "risk": np.zeros((16, 32), dtype=np.float32),
        }
        self.anchor_counts = {
            "action": 0,
            "resource": 0,
            "data": 0,
            "risk": 0,
        }

    def set_layer(self, layer_name: str, anchor_vectors: np.ndarray, count: int) -> None:
        """
        Set anchor vectors for a layer.

        Args:
            layer_name: Name of layer (action, resource, data, risk)
            anchor_vectors: Array of shape (16, 32) with encoded anchors
            count: Actual number of anchors (before padding)
        """
        self.layers[layer_name] = anchor_vectors
        self.anchor_counts[layer_name] = count

    def to_numpy(self) -> np.ndarray:
        """
        Convert to flattened numpy array.

        Returns:
            Flattened array of shape (2048,) = 4 × 16 × 32
        """
        stacked = np.stack([self.layers[name] for name in ["action", "resource", "data", "risk"]])
        return stacked.flatten()

    def to_dict(self) -> dict:
        """
        Convert to dictionary representation.

        Returns:
            Dict with layer arrays and anchor counts
        """
        return {
            "layers": {name: vec.tolist() for name, vec in self.layers.items()},
            "anchor_counts": self.anchor_counts,
        }


class PolicyEncoder(SemanticEncoder):
    """
    Semantic encoder for DesignBoundary to RuleVector.

    Encodes canonical DesignBoundary by:
    1. Extracting anchors for each constraint layer
    2. Encoding each anchor to 32-dim vector
    3. Aggregating with padding to 16×32 per layer
    4. Stacking to 4×16×32 RuleVector
    """

    MAX_ANCHORS_PER_LAYER = 16

    def __init__(self, embedding_model: str = SemanticEncoder.MODEL_NAME):
        """
        Initialize policy encoder.

        Args:
            embedding_model: Name of sentence-transformers model
        """
        super().__init__(embedding_model=embedding_model)

    def _extract_action_anchors(self, boundary: DesignBoundary) -> list[str]:
        """
        Extract anchor strings for action layer.

        Uses NL match.op as the action anchor.

        Args:
            boundary: DesignBoundary

        Returns:
            List of anchor strings
        """
        if boundary.match.op.strip():
            return [canonicalize_params(boundary.match.op)]
        return []

    def _extract_resource_anchors(self, boundary: DesignBoundary) -> list[str]:
        """
        Extract anchor strings for resource layer.

        Uses NL match.t as the resource anchor.

        Args:
            boundary: DesignBoundary

        Returns:
            List of anchor strings
        """
        if boundary.match.t.strip():
            return [canonicalize_params(boundary.match.t)]
        return []

    def _extract_data_anchors(self, boundary: DesignBoundary) -> list[str]:
        """
        Extract anchor strings for data layer.

        Uses NL match.p as the data anchor.

        Args:
            boundary: DesignBoundary

        Returns:
            List of anchor strings
        """
        if boundary.match.p:
            return [canonicalize_params(boundary.match.p)]
        return []

    def _extract_risk_anchors(self, boundary: DesignBoundary) -> list[str]:
        """
        Extract anchor strings for risk/context layer.

        Uses NL match.ctx as the risk anchor.

        Args:
            boundary: DesignBoundary

        Returns:
            List of anchor strings
        """
        if boundary.match.ctx:
            return [canonicalize_params(boundary.match.ctx)]
        return []

    def _encode_anchors(self, anchor_texts: list[str], layer_name: str) -> Tuple[np.ndarray, int]:
        """
        Encode list of anchors to padded array.

        Args:
            anchor_texts: List of anchor strings to encode
            layer_name: Name of layer (for logging and seed lookup)

        Returns:
            Tuple of (anchor_array, count) where:
            - anchor_array: (16, 32) array with encoded anchors (padded with zeros)
            - count: Actual number of anchors before padding
        """
        # Truncate if exceeds max
        if len(anchor_texts) > self.MAX_ANCHORS_PER_LAYER:
            logger.warning(
                f"Layer {layer_name} has {len(anchor_texts)} anchors, "
                f"truncating to {self.MAX_ANCHORS_PER_LAYER}"
            )
            anchor_texts = anchor_texts[: self.MAX_ANCHORS_PER_LAYER]

        # Encode each anchor
        anchor_vecs = []
        for text in anchor_texts:
            vec = self.encode_slot(text, layer_name)
            anchor_vecs.append(vec)

        # Pad to 16×32
        anchor_array = np.zeros((self.MAX_ANCHORS_PER_LAYER, 32), dtype=np.float32)
        for i, vec in enumerate(anchor_vecs):
            anchor_array[i] = vec

        return anchor_array, len(anchor_texts)

    def encode(self, boundary: DesignBoundary) -> RuleVector:
        """
        Encode canonical DesignBoundary to RuleVector.

        Steps:
        1. Extract anchors for each of 4 layers
        2. Encode each anchor to 32-dim
        3. Aggregate with padding to 16×32 per layer
        4. Stack to 4×16×32 RuleVector

        Args:
            boundary: Canonical DesignBoundary

        Returns:
            RuleVector with 4 layers of 16×32 anchor vectors
        """
        rule_vector = RuleVector()

        # Action layer
        action_anchors = self._extract_action_anchors(boundary)
        action_array, action_count = self._encode_anchors(action_anchors, "action")
        rule_vector.set_layer("action", action_array, action_count)

        # Resource layer
        resource_anchors = self._extract_resource_anchors(boundary)
        resource_array, resource_count = self._encode_anchors(resource_anchors, "resource")
        rule_vector.set_layer("resource", resource_array, resource_count)

        # Data layer
        data_anchors = self._extract_data_anchors(boundary)
        data_array, data_count = self._encode_anchors(data_anchors, "data")
        rule_vector.set_layer("data", data_array, data_count)

        # Risk layer
        risk_anchors = self._extract_risk_anchors(boundary)
        risk_array, risk_count = self._encode_anchors(risk_anchors, "risk")
        rule_vector.set_layer("risk", risk_array, risk_count)

        logger.debug(
            f"Encoded boundary {boundary.id}: "
            f"action={action_count}, resource={resource_count}, "
            f"data={data_count}, risk={risk_count}"
        )

        return rule_vector
