"""Static prompt sections — functions that return section strings or None.

Each function returns a prompt section string. The builder composes them.
"""
from __future__ import annotations

from typing import Any

from claw.core.prompt.templates import (
    ACTION_SAFETY,
    IDENTITY,
    MEMORY_GUIDANCE,
    OUTPUT,
    SYSTEM_RULES,
    TASK_EXECUTION,
    TONE,
    TOOL_ENTRY,
    TOOL_SECTION_HEADER,
    TOOL_USAGE_RULES,
)


def identity_section() -> str:
    return IDENTITY


def system_rules_section() -> str:
    return SYSTEM_RULES


def task_execution_section() -> str:
    return TASK_EXECUTION


def action_safety_section() -> str:
    return ACTION_SAFETY


def tool_usage_section(tools: list[Any] | None = None) -> str:
    """Build tool guidance from the actual enabled tool list.

    If no tools are provided, returns just the usage rules without
    the tool inventory.
    """
    if not tools:
        return TOOL_USAGE_RULES.strip()

    lines = [TOOL_SECTION_HEADER]
    for tool in tools:
        name = tool.name if hasattr(tool, "name") else str(tool)
        desc = tool.description if hasattr(tool, "description") else ""
        # Use first line of description only
        short_desc = desc.split("\n")[0] if desc else ""
        lines.append(TOOL_ENTRY.format(name=name, description=short_desc))
    lines.append(TOOL_USAGE_RULES)
    return "\n".join(lines)


def output_section() -> str:
    return OUTPUT


def tone_section() -> str:
    return TONE


def memory_guidance_section() -> str:
    return MEMORY_GUIDANCE
