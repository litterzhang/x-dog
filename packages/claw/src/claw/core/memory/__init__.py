"""Memory subsystem — storage, search, and built-in memory tool.

The memory domain owns its tool and registers it with the tool registry.
"""
from claw.core.memory.types import MemoryChunk  # noqa: F401 — re-export
from claw.core.tools.registry import register
from claw.core.tools.tool_memory import create_memory_tool

register("memory", create_memory_tool)
