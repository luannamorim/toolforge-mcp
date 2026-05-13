"""Unit tests for VoyageEmbedder — all httpx calls are mocked."""

from __future__ import annotations

import json
import logging
import math

import httpx
import pytest

from toolforge.agent.embedder import _VOYAGE_DIM, VoyageEmbedder


def _make_response(vecs: list[list[float]], status: int = 200) -> httpx.Response:
    body = json.dumps({"data": [{"embedding": v} for v in vecs]})
    return httpx.Response(status, content=body.encode(), headers={"content-type": "application/json"})


def _unit_vec(dim: int = _VOYAGE_DIM, first: float = 1.0) -> list[float]:
    """An already-normalized vector for a fake API response."""
    vec = [0.0] * dim
    vec[0] = first
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# Happy-path: embed() / embed_batch_documents()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_embed_sends_single_query_request(httpx_mock):
    vec = _unit_vec()
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        json={"data": [{"embedding": vec}]},
    )

    emb = VoyageEmbedder(api_key="test-key")
    result = emb.embed("hello world")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["input"] == ["hello world"]
    assert body["model"] == "voyage-3-lite"
    assert body["input_type"] == "query"
    assert req.headers["Authorization"] == "Bearer test-key"
    assert len(result) == _VOYAGE_DIM


@pytest.mark.unit
def test_embed_batch_documents_sends_one_request(httpx_mock):
    texts = ["desc A", "desc B", "desc C"]
    vecs = [_unit_vec() for _ in texts]
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        json={"data": [{"embedding": v} for v in vecs]},
    )

    emb = VoyageEmbedder(api_key="test-key")
    result = emb.embed_batch_documents(texts)

    assert len(httpx_mock.get_requests()) == 1
    body = json.loads(httpx_mock.get_request().content)
    assert body["input"] == texts
    assert body["input_type"] == "document"
    assert len(result) == 3
    assert len(result[0]) == _VOYAGE_DIM


@pytest.mark.unit
def test_result_vectors_are_l2_normalized(httpx_mock):
    raw = [0.3, 0.4] + [0.0] * (_VOYAGE_DIM - 2)
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        json={"data": [{"embedding": raw}]},
    )

    emb = VoyageEmbedder(api_key="test-key")
    result = emb.embed("text")
    norm = math.sqrt(sum(x * x for x in result))
    assert abs(norm - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Retry / fallback path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_timeout_then_success_retries_once(httpx_mock):
    vec = _unit_vec()
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        json={"data": [{"embedding": vec}]},
    )

    emb = VoyageEmbedder(api_key="test-key", timeout=1.0)
    result = emb.embed("text")
    assert result != [0.0] * _VOYAGE_DIM
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.unit
def test_two_consecutive_failures_return_zero_vector_and_log(httpx_mock, caplog):
    httpx_mock.add_exception(httpx.TimeoutException("timed out"))
    httpx_mock.add_exception(httpx.TimeoutException("timed out again"))

    emb = VoyageEmbedder(api_key="test-key", timeout=1.0)
    with caplog.at_level(logging.WARNING, logger="toolforge.agent.embedder"):
        result = emb.embed("text")

    assert result == [0.0] * _VOYAGE_DIM
    assert any("zero-vectors" in r.message for r in caplog.records)


@pytest.mark.unit
def test_5xx_response_falls_back_after_retry(httpx_mock, caplog):
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        status_code=503,
        content=b"service unavailable",
    )
    httpx_mock.add_response(
        url="https://api.voyageai.com/v1/embeddings",
        method="POST",
        status_code=503,
        content=b"service unavailable",
    )

    emb = VoyageEmbedder(api_key="test-key", timeout=1.0)
    with caplog.at_level(logging.WARNING, logger="toolforge.agent.embedder"):
        result = emb.embed_batch_documents(["a", "b"])

    assert result == [[0.0] * _VOYAGE_DIM, [0.0] * _VOYAGE_DIM]
    assert any("zero-vectors" in r.message for r in caplog.records)
