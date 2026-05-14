"""Embedder protocol and implementations.

The Embedder Protocol is the stable interface for rule 4 of the tool-selection
heuristic (cosine similarity). HashingEmbedder is the dev/fallback placeholder;
VoyageEmbedder is the production implementation (OQ#4 resolved: voyage-3-lite).
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Protocol, runtime_checkable

import httpx

DIM = 128

logger = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    embedder_id: str

    def embed(self, text: str) -> list[float]: ...
    def close(self) -> None: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        return [x / norm for x in vec]
    return vec


class HashingEmbedder:
    """Synthetic bag-of-words embedder for dev and testing.

    Tokenizes text on whitespace, maps each token to a hash bucket in a
    fixed-dim vector, then L2-normalizes so that dot product == cosine
    similarity. Collision probability is acceptable at DIM=128 for the
    short descriptions used in MCP tool catalogs.

    Not for production use — accuracy depends entirely on word overlap.
    """

    embedder_id = "hashing-v1"

    def __init__(self, dim: int = DIM) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in text.lower().split():
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            vec[bucket] += 1.0
        return _l2_normalize(vec)

    def close(self) -> None:
        pass


_VOYAGE_DIM = 512  # voyage-3-lite default output dimension


class VoyageEmbedder:
    """Voyage hosted embedder via REST (voyage-3-lite).

    Uses asymmetric retrieval: prompts embed as "query", tool descriptions
    as "document". Falls back to zero-vectors on API failure so rule 4
    gracefully falls through to rule 5 rather than crashing.

    Exposes embed_batch_documents() for one-shot catalog embedding.
    """

    embedder_id = "voyage-3-lite"

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"
    _MODEL = embedder_id

    def __init__(self, api_key: str, timeout: float = 5.0) -> None:
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def embed(self, text: str) -> list[float]:
        vecs = self._embed_batch([text], input_type="query")
        return vecs[0]

    def embed_batch_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed_batch(texts, input_type="document")

    def _embed_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        zero = [[0.0] * _VOYAGE_DIM for _ in texts]
        payload = {"input": texts, "model": self._MODEL, "input_type": input_type}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(2):
            try:
                resp = self._client.post(self._ENDPOINT, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                vecs = [item["embedding"] for item in data["data"]]
                return [_l2_normalize(v) for v in vecs]
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if attempt == 0:
                    logger.warning("voyage embed attempt 1 failed (%s), retrying", exc)
                    time.sleep(0.5)
                else:
                    logger.warning("voyage embed failed after retry: %s — using zero-vectors", exc)
        return zero

    def close(self) -> None:
        self._client.close()
