"""Tests for src/toolforge/prompts/ — loader and orchestrator wiring."""

from __future__ import annotations

import pytest

from toolforge.prompts import load_examples


@pytest.mark.unit
def test_load_examples_nonempty() -> None:
    content = load_examples()
    assert len(content) > 0
    assert "User:" in content


@pytest.mark.unit
def test_examples_reference_labeled_ids() -> None:
    content = load_examples()
    assert "ambig-mention-002" in content
    assert "cross-003" in content


@pytest.mark.unit
def test_orchestrator_system_text_includes_examples(settings, fake_mcp_pool, trace_writer, embedder) -> None:
    from toolforge.agent.orchestrator import Orchestrator

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings, embedder=embedder)
    assert load_examples() in orch._system_text
