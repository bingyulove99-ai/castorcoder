"""Minimal Model Context Protocol (MCP) client and tool adapter."""

from .client import MCPClient, MCPError
from .tool import MCPManager, MCPTool

__all__ = ["MCPClient", "MCPError", "MCPManager", "MCPTool"]
