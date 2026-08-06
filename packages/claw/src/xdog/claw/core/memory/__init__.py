"""Memory subsystem — storage, search, and built-in memory tool.

The memory domain owns its tool and registers it with the tool registry.
"""
from xdog.claw.core.memory.types import MemoryChunk  # noqa: F401 — re-export
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_memory import create_memory_tool

register("memory", create_memory_tool)
