"""System prompt builder: assembles the full system prompt from templates."""

from __future__ import annotations

from datetime import date
from typing import Any

from xdog.coding.config import RuntimeConfig
from xdog.coding.core.prompt_templates import (
    CUSTOM_INSTRUCTIONS_SECTION,
    ENVIRONMENT_SECTION,
    FILE_CONTEXT_SECTION,
    SYSTEM_PROMPT_HEADER,
    TOOL_DESCRIPTION_TEMPLATE,
    TOOL_SECTION_HEADER,
)


def _format_parameters(params: dict[str, Any]) -> str:
    """Format a JSON-schema-style parameters dict into a readable string."""
    props = params.get("properties", {})
    required_keys = set(params.get("required", []))
    lines: list[str] = []
    for name, schema in props.items():
        ptype = schema.get("type", "any")
        desc = schema.get("description", "")
        req = " (required)" if name in required_keys else ""
        lines.append(f"  - {name}: {ptype}{req} - {desc}")
    return "\n".join(lines) if lines else "  (none)"


def build_tool_section(tools: list[dict[str, Any]]) -> str:
    """Build the tools section of the system prompt."""
    parts = [TOOL_SECTION_HEADER]
    for tool in tools:
        parts.append(
            TOOL_DESCRIPTION_TEMPLATE.format(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=_format_parameters(tool.get("parameters", {})),
            )
        )
    return "\n".join(parts)


def build_environment_section(config: RuntimeConfig) -> str:
    """Build the environment info section."""
    pi = config.platform_info
    return ENVIRONMENT_SECTION.format(
        working_dir=config.working_dir,
        os_name=pi.os_name,
        os_version=pi.os_version,
        python_version=pi.python_version,
        shell=pi.shell,
        date=date.today().isoformat(),
    )


def build_file_context_section(file_entries: list[dict[str, Any]]) -> str:
    """Build the file-context section from processed file entries."""
    if not file_entries:
        return ""
    lines: list[str] = []
    for entry in file_entries:
        etype = entry.get("type", "")
        path = entry.get("path", "unknown")
        if etype == "file_content":
            content = entry.get("content", "")
            lines.append(f"### {path}\n```\n{content}\n```\n")
        elif etype == "image":
            lines.append(f"### {path}\n(image file)\n")
        elif etype == "file_reference":
            note = entry.get("note", "")
            lines.append(f"### {path}\n{note}\n")
    return FILE_CONTEXT_SECTION.format(file_entries="\n".join(lines))


def build_system_prompt(
    config: RuntimeConfig,
    tools: list[dict[str, Any]],
    *,
    file_entries: list[dict[str, Any]] | None = None,
    extra_context: str = "",
) -> str:
    """Assemble the complete system prompt.

    Parameters
    ----------
    config:
        The resolved runtime configuration.
    tools:
        List of tool definition dicts (name, description, parameters).
    file_entries:
        Optional processed file context entries.
    extra_context:
        Optional extra text appended at the end.
    """
    sections: list[str] = [
        SYSTEM_PROMPT_HEADER,
        build_environment_section(config),
        build_tool_section(tools),
    ]

    if config.custom_instructions:
        sections.append(
            CUSTOM_INSTRUCTIONS_SECTION.format(instructions=config.custom_instructions)
        )

    if file_entries:
        sections.append(build_file_context_section(file_entries))

    if extra_context:
        sections.append(extra_context)

    return "\n".join(sections)
