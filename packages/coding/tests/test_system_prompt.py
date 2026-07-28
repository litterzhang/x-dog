from pathlib import Path

from coding.config import PlatformInfo, RuntimeConfig
from coding.core.system_prompt import (
    build_environment_section,
    build_system_prompt,
    build_tool_section,
)


def test_build_tool_section():
    tools = [{
        "name": "my_tool",
        "description": "Does my tool thing",
        "parameters": {"properties": {"x": {"type": "string"}}}
    }]
    section = build_tool_section(tools)
    assert "Available Tools" in section
    assert "### my_tool" in section
    assert "Does my tool thing" in section

def test_build_environment_section():
    config = RuntimeConfig(
        model="sonnet",
        thinking_level="normal",
        allowed_tools=None,
        custom_instructions="",
        extensions=[],
        working_dir=Path("/test"),
        platform_info=PlatformInfo(
            os_name="TestOS",
            os_version="1.0",
            python_version="3.11",
            shell="bash",
            home_dir=Path("/home/user")
        )
    )
    section = build_environment_section(config)
    assert "Environment" in section
    assert "/test" in section
    assert "TestOS" in section

def test_build_system_prompt():
    config = RuntimeConfig(
        model="sonnet",
        thinking_level="normal",
        allowed_tools=None,
        custom_instructions="Be concise.",
        extensions=[],
        working_dir=Path("/test"),
        platform_info=PlatformInfo(
            os_name="TestOS",
            os_version="1.0",
            python_version="3.11",
            shell="bash",
            home_dir=Path("/home/user")
        )
    )

    tools = [{"name": "tool1"}]
    file_entries = [{"type": "file_content", "path": "x.txt", "content": "content x"}]

    prompt = build_system_prompt(
        config,
        tools,
        file_entries=file_entries,
        extra_context="Extra text here."
    )

    assert "interactive coding agent" in prompt
    assert "Environment" in prompt
    assert "Available Tools" in prompt
    assert "tool1" in prompt
    assert "Custom Instructions" in prompt
    assert "Be concise." in prompt
    assert "File Context" in prompt
    assert "content x" in prompt
    assert "Extra text here." in prompt
