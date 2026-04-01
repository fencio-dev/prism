"""
Intent Encoder for 128-dimensional semantic intent vectors.

Subclass of SemanticEncoder that:
1. Extracts 4 semantic slots from canonical IntentEvent: action, resource, data, risk
2. Encodes each slot to 32-dimensional vector
3. Concatenates to 128-dimensional intent vector
4. Per-slot normalization (not global)

The base class handles:
- Model loading (sentence-transformers)
- Embedding generation (384d)
- Projection matrix creation (sparse random projection)
- Caching

This class adds:
- Slot extraction from IntentEvent
- Text assembly for each slot
- Vector aggregation

Example:
    encoder = IntentEncoder()
    canonical_intent = IntentEvent(...)
    vector = encoder.encode(canonical_intent)  # Returns np.ndarray of shape (128,)
"""

import logging
import numpy as np

from app.models import IntentEvent
from app.services.semantic_encoder import SemanticEncoder
from app.services.param_canonicalizer import canonicalize_params

logger = logging.getLogger(__name__)


class IntentEncoder(SemanticEncoder):
    """
    Semantic encoder for IntentEvent to 128-dimensional vectors.

    Encodes canonical IntentEvent by:
    1. Building 4 slot strings (action, resource, data, risk)
    2. Encoding each to 384-dim
    3. Projecting each to 32-dim
    4. Concatenating to 128-dim
    5. Per-slot normalization (L2)
    """

    def __init__(self, embedding_model: str = SemanticEncoder.MODEL_NAME):
        """
        Initialize intent encoder.

        Args:
            embedding_model: Name of sentence-transformers model
        """
        super().__init__(embedding_model=embedding_model)

    def _build_action_slot(self, event: IntentEvent) -> str:
        """
        Build action slot string for encoding.

        Args:
            event: Canonical IntentEvent

        Returns:
            Slot text string
        """
        return canonicalize_params(event.op)

    def _build_resource_slot(self, event: IntentEvent) -> str:
        """
        Build resource slot string for encoding.

        Args:
            event: Canonical IntentEvent

        Returns:
            Slot text string
        """
        return canonicalize_params(event.t)

    def _build_data_slot(self, event: IntentEvent) -> str:
        """
        Build data slot string for encoding.

        Args:
            event: Canonical IntentEvent

        Returns:
            Slot text string
        """
        if (
            event.source_layer == "llm"
            and event.destination_layer == "tool"
            and event.llm_tool_intent
        ):
            return canonicalize_params(event.llm_tool_intent)
        return canonicalize_params(event.p)

    def _build_risk_slot(self, event: IntentEvent) -> str:
        """
        Build risk slot string for encoding.

        Args:
            event: Canonical IntentEvent

        Returns:
            Slot text string
        """
        if event.ctx is None or event.ctx.initial_request is None:
            return ""
        return canonicalize_params(event.ctx.initial_request)

    def encode(self, event: IntentEvent) -> np.ndarray:
        """
        Encode canonical IntentEvent to 128-dimensional vector.

        Steps:
        1. Build 4 slot strings (action, resource, data, risk)
        2. Encode each slot using base class method
        3. Concatenate to 128-dim
        4. Each slot is per-slot normalized (no global normalization)

        Args:
            event: Canonical IntentEvent

        Returns:
            128-dimensional vector (float32), per-slot normalized
        """
        # Build slot strings
        slot_texts = {
            "action": self._build_action_slot(event),
            "resource": self._build_resource_slot(event),
            "data": self._build_data_slot(event),
            "risk": self._build_risk_slot(event),
        }

        # Encode and project each slot
        slot_vectors = []
        for slot_name in ["action", "resource", "data", "risk"]:
            text = slot_texts[slot_name]
            slot_vector = self.encode_slot(text, slot_name)
            slot_vectors.append(slot_vector)

        # Concatenate to 128-dim
        vector_128 = np.concatenate(slot_vectors)

        return vector_128
