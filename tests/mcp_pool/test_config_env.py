"""Unit tests for MCPServerConfig.resolved_env()."""

from __future__ import annotations

import pytest

from toolforge.models.catalog import MCPServerConfig


def _cfg(env: dict[str, str] | None = None) -> MCPServerConfig:
    return MCPServerConfig(id="s", command="cmd", env=env or {})


@pytest.mark.unit
def test_resolved_env_empty_config_inherits_os_environ(monkeypatch):
    monkeypatch.setenv("_TF_TEST_KEY", "parent_value")
    result = _cfg().resolved_env()
    assert result["_TF_TEST_KEY"] == "parent_value"


@pytest.mark.unit
def test_resolved_env_expands_dollar_brace_syntax(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "tok123")
    result = _cfg(env={"API_KEY": "${MY_TOKEN}"}).resolved_env()
    assert result["API_KEY"] == "tok123"


@pytest.mark.unit
def test_resolved_env_expands_plain_dollar_syntax(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "tok456")
    result = _cfg(env={"API_KEY": "$MY_TOKEN"}).resolved_env()
    assert result["API_KEY"] == "tok456"


@pytest.mark.unit
def test_resolved_env_missing_var_leaves_placeholder(monkeypatch):
    # os.path.expandvars on POSIX leaves unset references as-is (${VAR} unchanged).
    monkeypatch.delenv("_TF_MISSING_VAR", raising=False)
    result = _cfg(env={"KEY": "${_TF_MISSING_VAR}"}).resolved_env()
    assert result["KEY"] == "${_TF_MISSING_VAR}"


@pytest.mark.unit
def test_resolved_env_config_overrides_os_environ(monkeypatch):
    monkeypatch.setenv("PATH", "/original")
    result = _cfg(env={"PATH": "/override"}).resolved_env()
    assert result["PATH"] == "/override"


@pytest.mark.unit
def test_resolved_env_contains_os_environ_keys():
    result = _cfg().resolved_env()
    # PATH must always be present in a sane POSIX environment
    assert "PATH" in result


@pytest.mark.unit
def test_resolved_env_literal_value_passed_through():
    result = _cfg(env={"STATIC": "hello"}).resolved_env()
    assert result["STATIC"] == "hello"


@pytest.mark.unit
def test_resolved_env_returns_new_dict_each_call():
    cfg = _cfg(env={"K": "v"})
    a = cfg.resolved_env()
    b = cfg.resolved_env()
    assert a is not b
