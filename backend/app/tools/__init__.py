"""Agent tools. Importing this package registers every tool in `TOOLS`."""

from app.tools import database, git, logs, search  # noqa: F401 - registers the tools
from app.tools.registry import TOOLS, ToolContext, ToolError, call_tool, openai_schemas

__all__ = ["TOOLS", "ToolContext", "ToolError", "call_tool", "openai_schemas"]
