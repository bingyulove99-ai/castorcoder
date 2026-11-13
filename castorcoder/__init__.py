"""CastorCoder public API."""

__version__ = "1.3.0"

from .agent import Agent, AgentResult, AgentStats
from .config import Config
from .events import AgentEvent, EventBus, JsonlTrace
from .index import CodeIndex, HashingEmbedder, SearchHit, make_embedder
from .llm import LLM, LiteLLM
from .mcp import MCPClient, MCPError, MCPManager, MCPTool
from .memory import MemoryRecord, MemoryStore, MemoryWriter
from .metrics import TurnReceipt, UsageLedger
from .tools import ALL_TOOLS, create_tools
from .trim import shrink_seen
from .workspace import Workspace, WorkspaceViolation

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentResult",
    "AgentStats",
    "ALL_TOOLS",
    "CodeIndex",
    "Config",
    "EventBus",
    "HashingEmbedder",
    "JsonlTrace",
    "LLM",
    "LiteLLM",
    "MCPClient",
    "MCPError",
    "MCPManager",
    "MCPTool",
    "MemoryRecord",
    "MemoryStore",
    "MemoryWriter",
    "SearchHit",
    "TurnReceipt",
    "UsageLedger",
    "Workspace",
    "WorkspaceViolation",
    "__version__",
    "create_tools",
    "make_embedder",
    "shrink_seen",
]
