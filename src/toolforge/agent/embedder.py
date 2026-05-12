"""Embedder protocol and synthetic implementation.

The Embedder Protocol is the stable interface for rule 4 of the tool-selection
heuristic (cosine similarity). HashingEmbedder is the Phase 2 placeholder —
a pure-Python bag-of-words over token hashes, L2-normalized so dot product
equals cosine similarity. Swap it for Voyage hosted or local BGE when OQ#4
resolves without touching any caller.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

DIM = 128


@runtime_checkable
class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Synthetic bag-of-words embedder for dev and testing.

    Tokenizes text on whitespace, maps each token to a hash bucket in a
    fixed-dim vector, then L2-normalizes so that dot product == cosine
    similarity. Collision probability is acceptable at DIM=128 for the
    short descriptions used in MCP tool catalogs.

    Not for production use — accuracy depends entirely on word overlap.
    """

    def __init__(self, dim: int = DIM) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in text.lower().split():
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
