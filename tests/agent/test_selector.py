"""Tests for the 5-rule tool-selection heuristic.

Strategy:
- One parametrized test per rule (rule fires, alternatives recorded).
- Fall-through tests: each rule returns None and the next rule wins.
- End-to-end: select_server with a 2-candidate catalog verifies the full chain.

LLM-independence: no Anthropic calls; pure function tests against synthetic
ToolDescriptors with controlled embeddings injected via _FixedEmbedder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.conftest import (
    FS_READ_TOOL,
    GH_READ_TOOL,
    SYNTHETIC_PRIORITY,
)
from toolforge.agent.selector import (
    COSINE_MARGIN_DEFAULT,
    SelectionContext,
    _rule_argument_type_match,
    _rule_cosine_similarity,
    _rule_explicit_mention,
    _rule_priority_order,
    _rule_session_recency,
    select_server,
)
from toolforge.models.catalog import ToolDescriptor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    prompt: str = "",
    session: list[str] | None = None,
    priority: list[str] | None = None,
    prompt_embedding: list[float] | None = None,
    cosine_margin: float = COSINE_MARGIN_DEFAULT,
) -> SelectionContext:
    return SelectionContext(
        prompt=prompt,
        session_used_servers=session or [],
        priority_order=priority or SYNTHETIC_PRIORITY,
        prompt_embedding=prompt_embedding,
        cosine_margin=cosine_margin,
    )


def _embed(text: str, dim: int = 4) -> list[float]:
    """Tiny hand-crafted embedding for deterministic cosine tests."""
    # Returns a unit vector biased by position of 'g' in text (for github vs filesystem).
    # Just used to inject known vectors; not realistic.
    vec = [float(ord(c) % 2) for c in text[:dim]]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def _fixed_embedder(mapping: dict[str, list[float]]):
    """Returns a duck-typed embedder that maps text → known vector."""
    default = [0.0, 0.0, 0.0, 0.0]
    return SimpleNamespace(embed=lambda text: mapping.get(text, default))


# Shared candidates list (filesystem vs github, same tool name)
READ_CANDIDATES = [FS_READ_TOOL, GH_READ_TOOL]


# ---------------------------------------------------------------------------
# select_server: edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_select_server_raises_on_empty_candidates():
    with pytest.raises(ValueError, match="No candidates"):
        select_server("read_file", [], _ctx())


@pytest.mark.unit
def test_select_server_single_candidate_shortcut():
    selected, rule, alternatives = select_server("read_file", [FS_READ_TOOL], _ctx())
    assert selected.server_id == "filesystem"
    assert rule == "single-candidate"
    assert alternatives == []


# ---------------------------------------------------------------------------
# Rule 1: explicit mention
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "prompt, expected_server",
    [
        ("on github, read foo.py", "github"),
        ("use filesystem to read config", "filesystem"),
    ],
)
def test_rule1_explicit_mention(prompt, expected_server):
    result = _rule_explicit_mention(READ_CANDIDATES, _ctx(prompt=prompt))
    assert result is not None
    selected, rule, alternatives = result
    assert selected.server_id == expected_server
    assert rule == "explicit-mention"
    assert expected_server not in alternatives


@pytest.mark.unit
def test_rule1_no_match_returns_none():
    assert _rule_explicit_mention(READ_CANDIDATES, _ctx(prompt="read a file")) is None


@pytest.mark.unit
def test_rule1_ambiguous_both_mentioned_returns_none():
    assert _rule_explicit_mention(
        READ_CANDIDATES, _ctx(prompt="read from github or filesystem")
    ) is None


# ---------------------------------------------------------------------------
# Rule 2: argument-type match
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule2_filesystem_args_only_validate_filesystem():
    # {"path": "..."} validates filesystem schema but NOT github (missing owner/repo)
    result = _rule_argument_type_match(READ_CANDIDATES, tool_input={"path": "/tmp/x"})
    assert result is not None
    selected, rule, alternatives = result
    assert selected.server_id == "filesystem"
    assert rule == "argument-type"
    assert "github" in alternatives


@pytest.mark.unit
def test_rule2_github_args_only_validate_github():
    # filesystem schema allows additionalProperties by default, so we need additionalProperties:false
    # to make {owner, repo, path} exclusively match the github schema.
    strict_fs = FS_READ_TOOL.model_copy(
        update={
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            }
        }
    )
    result = _rule_argument_type_match(
        [strict_fs, GH_READ_TOOL],
        tool_input={"owner": "acme", "repo": "core", "path": "README.md"},
    )
    assert result is not None
    selected, rule, _ = result
    assert selected.server_id == "github"
    assert rule == "argument-type"


@pytest.mark.unit
def test_rule2_none_input_returns_none():
    assert _rule_argument_type_match(READ_CANDIDATES, tool_input=None) is None


@pytest.mark.unit
def test_rule2_ambiguous_both_validate_returns_none():
    # A schema that accepts any object — both would validate {} as tool_input
    open_schema = ToolDescriptor(
        name="read_file", description="x",
        input_schema={"type": "object"}, server_id="open",
    )
    result = _rule_argument_type_match([FS_READ_TOOL, open_schema], tool_input={})
    # filesystem requires "path"; {} fails → only open_schema validates
    assert result is not None
    assert result[0].server_id == "open"


# ---------------------------------------------------------------------------
# Rule 3: session recency
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule3_most_recent_wins():
    ctx = _ctx(session=["github", "filesystem", "github"])
    result = _rule_session_recency(READ_CANDIDATES, ctx)
    assert result is not None
    selected, rule, _ = result
    assert selected.server_id == "github"
    assert rule == "session-recency"


@pytest.mark.unit
def test_rule3_empty_session_returns_none():
    assert _rule_session_recency(READ_CANDIDATES, _ctx(session=[])) is None


@pytest.mark.unit
def test_rule3_session_has_no_candidate_returns_none():
    ctx = _ctx(session=["slack"])  # slack is not a candidate for read_file
    assert _rule_session_recency(READ_CANDIDATES, ctx) is None


@pytest.mark.unit
def test_rule3_records_alternatives():
    ctx = _ctx(session=["filesystem"])
    _, _, alternatives = _rule_session_recency(READ_CANDIDATES, ctx)
    assert "github" in alternatives


# ---------------------------------------------------------------------------
# Rule 4: cosine similarity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule4_high_similarity_wins():
    # Inject embeddings: prompt matches github description vector closely
    prompt_vec = [1.0, 0.0, 0.0, 0.0]
    fs_vec = [0.0, 1.0, 0.0, 0.0]   # orthogonal to prompt
    gh_vec = [0.95, 0.31, 0.0, 0.0]  # cosine ≈ 0.95

    fs = FS_READ_TOOL.model_copy(update={"description_embedding": fs_vec})
    gh = GH_READ_TOOL.model_copy(update={"description_embedding": gh_vec})

    ctx = _ctx(prompt_embedding=prompt_vec, cosine_margin=0.05)
    result = _rule_cosine_similarity([fs, gh], ctx)
    assert result is not None
    selected, rule, alternatives = result
    assert selected.server_id == "github"
    assert rule == "cosine-similarity"
    assert "filesystem" in alternatives


@pytest.mark.unit
def test_rule4_below_margin_returns_none():
    # Two candidates with nearly identical cosine scores
    prompt_vec = [1.0, 0.0, 0.0, 0.0]
    vec_a = [0.9, 0.44, 0.0, 0.0]  # cosine ≈ 0.90
    vec_b = [0.88, 0.47, 0.0, 0.0]  # cosine ≈ 0.88 — within margin 0.05

    fs = FS_READ_TOOL.model_copy(update={"description_embedding": vec_a})
    gh = GH_READ_TOOL.model_copy(update={"description_embedding": vec_b})

    ctx = _ctx(prompt_embedding=prompt_vec, cosine_margin=0.05)
    assert _rule_cosine_similarity([fs, gh], ctx) is None


@pytest.mark.unit
def test_rule4_no_prompt_embedding_returns_none():
    ctx = _ctx(prompt_embedding=None)
    fs = FS_READ_TOOL.model_copy(update={"description_embedding": [1.0, 0.0]})
    assert _rule_cosine_similarity([fs], ctx) is None


@pytest.mark.unit
def test_rule4_no_description_embeddings_returns_none():
    # Candidates have no embeddings
    ctx = _ctx(prompt_embedding=[1.0, 0.0])
    assert _rule_cosine_similarity(READ_CANDIDATES, ctx) is None


# ---------------------------------------------------------------------------
# Rule 5: priority order
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule5_first_in_priority_wins():
    ctx = _ctx(priority=["github", "filesystem"])
    result = _rule_priority_order(READ_CANDIDATES, ctx)
    assert result is not None
    selected, rule, alternatives = result
    assert selected.server_id == "github"
    assert rule == "priority-order"
    assert "filesystem" in alternatives


@pytest.mark.unit
def test_rule5_unregistered_server_goes_last():
    unregistered = ToolDescriptor(
        name="read_file", description="x",
        input_schema={}, server_id="unknown",
    )
    ctx = _ctx(priority=["filesystem"])
    result = _rule_priority_order([unregistered, FS_READ_TOOL], ctx)
    assert result[0].server_id == "filesystem"


@pytest.mark.unit
@pytest.mark.parametrize("priority", [["filesystem", "github"], ["github", "filesystem"]])
def test_rule5_respects_configured_order(priority):
    ctx = _ctx(priority=priority)
    result = _rule_priority_order(READ_CANDIDATES, ctx)
    assert result is not None
    assert result[0].server_id == priority[0]
    assert result[1] == "priority-order"


# ---------------------------------------------------------------------------
# Fall-through chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fallthrough_rule1_miss_goes_to_rule2():
    """No explicit mention → falls to argument-type."""
    result = select_server(
        "read_file",
        READ_CANDIDATES,
        _ctx(prompt="read a file"),  # no server name
        tool_input={"path": "/x"},   # only filesystem validates
    )
    assert result[1] == "argument-type"
    assert result[0].server_id == "filesystem"


@pytest.mark.unit
def test_fallthrough_rule1_rule2_miss_goes_to_rule3():
    """No mention, ambiguous args → falls to session recency."""
    # Both candidates require no args or share common args → make rule 2 ambiguous
    open_fs = FS_READ_TOOL.model_copy(
        update={"input_schema": {"type": "object"}}
    )
    open_gh = GH_READ_TOOL.model_copy(
        update={"input_schema": {"type": "object"}}
    )
    ctx = _ctx(prompt="read", session=["github"])
    result = select_server("read_file", [open_fs, open_gh], ctx, tool_input={})
    assert result[1] == "session-recency"
    assert result[0].server_id == "github"


@pytest.mark.unit
def test_fallthrough_all_miss_goes_to_rule5():
    """No mention, ambiguous args, no session, no embeddings → priority order."""
    open_fs = FS_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
    open_gh = GH_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
    ctx = _ctx(prompt="read", priority=["github", "filesystem"])
    result = select_server("read_file", [open_fs, open_gh], ctx, tool_input={})
    assert result[1] == "priority-order"
    assert result[0].server_id == "github"


# ---------------------------------------------------------------------------
# End-to-end: select_server with HashingEmbedder embeddings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_select_server_rule4_with_hashing_embedder():
    """HashingEmbedder distinguishes 'repository file' prompt → github over filesystem.

    Prompt contains neither server id (so rule 1 falls through), no tool_input (rule 2),
    and empty session (rule 3). Rule 4 fires via cosine similarity.
    """
    from toolforge.agent.embedder import HashingEmbedder

    emb = HashingEmbedder()
    # Prompt has 'repository' — a token unique to the github description
    prompt = "retrieve contents of a repository file"
    prompt_vec = emb.embed(prompt)

    fs = FS_READ_TOOL.model_copy(
        update={"description_embedding": emb.embed(FS_READ_TOOL.description)}
    )
    gh = GH_READ_TOOL.model_copy(
        update={"description_embedding": emb.embed(GH_READ_TOOL.description)}
    )

    # No tool_input (rule 2 falls through); empty session (rule 3 falls through)
    ctx = _ctx(prompt=prompt, prompt_embedding=prompt_vec, cosine_margin=0.001)
    result = select_server("read_file", [fs, gh], ctx, tool_input=None)
    # 'repository' token overlaps github description → higher cosine score
    assert result[0].server_id == "github"
    assert result[1] == "cosine-similarity"


@pytest.mark.unit
def test_alternatives_recorded_in_result():
    """alternatives contains the server_id of the losing candidate."""
    _, _, alternatives = select_server(
        "read_file",
        READ_CANDIDATES,
        _ctx(prompt="on github"),
    )
    assert alternatives == ["filesystem"]
