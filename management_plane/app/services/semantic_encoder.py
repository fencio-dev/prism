"""
Base Semantic Encoder for multi-modal semantic security encoding.

Shared responsibilities for all encoder subclasses:
- Load sentence-transformers model (all-MiniLM-L6-v2, 384 dimensions)
- Generate deterministic projection matrices from seed values
- Encode text inputs to 384-dimensional embeddings
- Project embeddings to target dimensions with sparse random projection
- Provide LRU caching for text embeddings
- Configuration management

Subclasses (IntentEncoder, PolicyEncoder) override:
- Slot extraction: Define which semantic slots to use
- Aggregation: Define how to combine slots into final vectors

Architecture:
```
Text Input → Tokenizer → BERT → 384d Embedding
                                      ↓
                              Projection Matrix
                                      ↓
                        32d Projected Vector (per slot)
                                      ↓
                            Normalize per-slot
                                      ↓
                    [slot_1 (32d), slot_2 (32d), ...]
                                      ↓
                    Aggregate to final vector (subclass-specific)
```

Performance Targets:
- Model load: ~1 second (lazy-loaded once)
- Embedding: ~5ms per text
- Projection: <1ms per slot
- Total per intent: <10ms (4 slots × ~2ms)
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Global model instance (lazy loaded per process)
_MODEL: Optional[SentenceTransformer] = None
_PROJECTION_MATRICES: dict[str, np.ndarray] = {}


class SemanticEncoder:
    """
    Base class for semantic encoders.

    Provides shared functionality for embedding and projection.
    Subclasses implement slot extraction and vector aggregation.
    """

    # Model configuration (shared across subclasses)
    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    MODEL_DIM = 384
    SLOT_DIM = 32

    def __init__(
        self,
        embedding_model: str = MODEL_NAME,
        projection_seeds: Optional[dict[str, int]] = None,
    ):
        """
        Initialize semantic encoder.

        Args:
            embedding_model: Name of sentence-transformers model
            projection_seeds: Dict mapping slot names to random seeds
                              Default: {"action": 42, "resource": 43, "data": 44, "risk": 45}
        """
        self.embedding_model = embedding_model
        self.projection_seeds = projection_seeds or {
            "action": 42,
            "resource": 43,
            "data": 44,
            "risk": 45,
        }

    @staticmethod
    def get_encoder_model(model_name: str = MODEL_NAME) -> SentenceTransformer:
        """
        Get singleton sentence-transformers model.

        Lazy-loads on first access. Safe for multi-threaded use.

        Args:
            model_name: Name of sentence-transformers model

        Returns:
            SentenceTransformer model instance
        """
        global _MODEL

        if _MODEL is None:
            logger.info(f"Loading sentence-transformers model: {model_name}")
            _MODEL = SentenceTransformer(model_name)
            logger.info(f"Model loaded: {_MODEL.get_sentence_embedding_dimension()}d embeddings")

        return _MODEL

    @staticmethod
    def create_sparse_projection_matrix(
        input_dim: int,
        output_dim: int,
        seed: int,
        sparsity: float = 0.66,
    ) -> np.ndarray:
        """
        Create sparse random projection matrix.

        Uses sparse random projection from Johnson-Lindenstrauss lemma:
        - Each element: +√3 (prob 1/6), 0 (prob 2/3), -√3 (prob 1/6)
        - Preserves distances for high-dimensional data
        - Deterministic with fixed seed for reproducibility

        Args:
            input_dim: Input dimensionality (e.g., 384)
            output_dim: Output dimensionality (e.g., 32)
            seed: Random seed for determinism
            sparsity: Fraction of zeros (default 2/3)

        Returns:
            Sparse projection matrix of shape (output_dim, input_dim)
        """
        rng = np.random.RandomState(seed)
        s = 1 / (1 - sparsity)  # s=3 for sparsity=0.66
        sqrt_s = np.sqrt(s)

        # Probabilities: [+√3, 0, -√3] for sparsity=2/3
        prob_pos = 1 / (2 * s)  # 1/6
        prob_zero = 1 - 1 / s  # 2/3
        prob_neg = 1 / (2 * s)  # 1/6

        matrix = rng.choice(
            [sqrt_s, 0.0, -sqrt_s],
            size=(output_dim, input_dim),
            p=[prob_pos, prob_zero, prob_neg],
        )

        return matrix.astype(np.float32)

    @staticmethod
    def get_projection_matrix(
        slot_name: str,
        seed: int,
        input_dim: int = MODEL_DIM,
        output_dim: int = SLOT_DIM,
    ) -> np.ndarray:
        """
        Get or create projection matrix for a specific slot.

        Cached globally to ensure deterministic projections across calls.

        Args:
            slot_name: Name of slot (e.g., "action", "resource")
            seed: Random seed for this slot
            input_dim: Input dimensionality (default 384)
            output_dim: Output dimensionality (default 32)

        Returns:
            Projection matrix of shape (output_dim, input_dim)
        """
        global _PROJECTION_MATRICES

        cache_key = f"{slot_name}_{input_dim}_{output_dim}_{seed}"

        if cache_key not in _PROJECTION_MATRICES:
            logger.debug(f"Creating projection matrix for {slot_name} (seed={seed})")
            _PROJECTION_MATRICES[cache_key] = SemanticEncoder.create_sparse_projection_matrix(
                input_dim=input_dim,
                output_dim=output_dim,
                seed=seed,
            )

        return _PROJECTION_MATRICES[cache_key]

    @lru_cache(maxsize=10000)
    def encode_text_cached(self, text: str) -> np.ndarray:
        """
        Encode text to 384-dimensional vector with LRU caching.

        Cache key is the text string. Cache improves performance when
        encoding similar boundaries or intents with repeated text.

        Args:
            text: Input text string

        Returns:
            384-dimensional embedding (float32, not normalized)
        """
        model = self.get_encoder_model(self.embedding_model)
        embedding = model.encode(
            text,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return embedding.astype(np.float32)

    def project_and_normalize(
        self,
        embedding_384: np.ndarray,
        slot_name: str,
    ) -> np.ndarray:
        """
        Project 384-dimensional embedding to 32-dimensional and normalize.

        Args:
            embedding_384: 384-dimensional embedding vector
            slot_name: Name of slot (for seed lookup)

        Returns:
            32-dimensional normalized vector (unit norm)
        """
        # Get projection matrix for this slot
        projection = self.get_projection_matrix(
            slot_name=slot_name,
            seed=self.projection_seeds[slot_name],
        )

        # Project
        projected = projection @ embedding_384

        # Normalize per-slot
        norm = np.linalg.norm(projected)
        if norm > 0:
            projected = projected / norm

        return projected

    def encode_slot(self, text: str, slot_name: str) -> np.ndarray:
        """
        Encode a single slot string to 32-dimensional normalized vector.

        Steps:
        1. Encode text to 384-dim using sentence-transformers
        2. Project to 32-dim using sparse random projection
        3. L2 normalize

        Args:
            text: Slot text string
            slot_name: Name of slot (for seed lookup)

        Returns:
            32-dimensional normalized vector
        """
        embedding_384 = self.encode_text_cached(text)
        return self.project_and_normalize(embedding_384, slot_name)

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self.encode_text_cached.cache_clear()
        logger.info("Semantic encoder cache cleared")

    def get_cache_stats(self) -> dict:
        """Get embedding cache statistics."""
        cache_info = self.encode_text_cached.cache_info()
        return {
            "hits": cache_info.hits,
            "misses": cache_info.misses,
            "size": cache_info.currsize,
            "maxsize": cache_info.maxsize,
        }
