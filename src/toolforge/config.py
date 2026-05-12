from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from toolforge.models.catalog import MCPServerConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    trace_verbose: bool = False
    redis_url: str = "redis://localhost:6379"
    trace_sink: Path = Path("logs/traces.jsonl")
    mcp_servers_config: Path = Path("mcp.servers.json")

    @property
    def mcp_servers(self) -> list[MCPServerConfig]:
        raw = json.loads(self.mcp_servers_config.read_text())
        return [MCPServerConfig(**s) for s in raw["servers"]]
