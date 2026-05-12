from pydantic import BaseModel


class MCPServerConfig(BaseModel):
    id: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}


class ToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict
    server_id: str
    description_embedding: list[float] | None = None


class ToolCatalog(BaseModel):
    tools: list[ToolDescriptor]

    def for_server(self, server_id: str) -> list[ToolDescriptor]:
        return [t for t in self.tools if t.server_id == server_id]

    def find_candidates(self, tool_name: str) -> list[ToolDescriptor]:
        return [t for t in self.tools if t.name == tool_name]
