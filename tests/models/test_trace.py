"""Unit tests for TraceRecord model."""

from __future__ import annotations

import pytest

from toolforge.models.trace import SCHEMA_VERSION, TraceRecord


@pytest.mark.unit
def test_trace_record_retry_fields_default():
    record = TraceRecord(
        session_id="s1",
        step=1,
        server="filesystem",
        tool="read_file",
        arguments_hash="abc",
        latency_ms=1.0,
        success=True,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
        selection_rule="single-candidate",
    )
    assert record.attempt == 1
    assert record.retries == 0
    assert record.retry_reason is None
    assert record.schema_version == SCHEMA_VERSION

    data = record.model_dump(exclude_none=True)
    assert data["attempt"] == 1
    assert data["retries"] == 0
    assert "retry_reason" not in data  # None → excluded


@pytest.mark.unit
def test_trace_record_retry_fields_populated():
    record = TraceRecord(
        session_id="s1",
        step=1,
        server="filesystem",
        tool="read_file",
        arguments_hash="abc",
        latency_ms=1500.0,
        success=True,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
        selection_rule="single-candidate",
        attempt=3,
        retries=2,
        retry_reason="TimeoutError",
    )
    data = record.model_dump(exclude_none=True)
    assert data["attempt"] == 3
    assert data["retries"] == 2
    assert data["retry_reason"] == "TimeoutError"
