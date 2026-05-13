import os

from pydantic import BaseModel


class MCPServerConfig(BaseModel):
    id: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}

    def resolved_env(self) -> dict[str, str]:
        """Merge config env (with ${VAR} expansion) onto os.environ.

        Subprocess inherits PATH/HOME from parent; config values win on
        collision. Unset referenced vars are left as the literal ${VAR}
        placeholder (POSIX os.path.expandvars behaviour). Note: this passes
        the full parent env, not the MCP SDK's restricted DEFAULT_INHERITED_ENV_VARS
        subset, so npx/node can be found on PATH.
        """
        merged = dict(os.environ)
        for key, value in self.env.items():
            merged[key] = os.path.expandvars(value)
        return merged


class ToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict
    server_id: str
    description_embedding: list[float] | None = None


class ToolSummary(BaseModel):
    """Public-facing shape for /tools — ToolDescriptor without the internal embedding vector."""

    name: str
    description: str
    input_schema: dict
    server_id: str


class ToolCatalog(BaseModel):
    tools: list[ToolDescriptor]

    def for_server(self, server_id: str) -> list[ToolDescriptor]:
        return [t for t in self.tools if t.server_id == server_id]

    def find_candidates(self, tool_name: str) -> list[ToolDescriptor]:
        return [t for t in self.tools if t.name == tool_name]
