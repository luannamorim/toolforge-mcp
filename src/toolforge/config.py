from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from toolforge.models.catalog import MCPServerConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    trace_verbose: bool = False
    retry_max_attempts: int = 3
    retry_base_delay_ms: int = 500
    retry_backoff_factor: float = 2.0
    retry_jitter: bool = True
    redis_url: str = "redis://localhost:6379"
    catalog_cache_backend: Literal["redis", "memory"] = "redis"
    trace_sink: Path = Path("logs/traces.jsonl")
    mcp_servers_config: Path = Path("mcp.servers.json")
    cost_ceiling_usd: float = 0.10
    max_request_bytes: int = 32 * 1024
    voyage_api_key: str = ""
    embedder_backend: Literal["voyage", "hashing"] = "voyage"

    @property
    def mcp_servers(self) -> list[MCPServerConfig]:
        raw = json.loads(self.mcp_servers_config.read_text())
        return [MCPServerConfig(**s) for s in raw["servers"]]
