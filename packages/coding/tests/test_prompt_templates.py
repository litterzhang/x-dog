from xdog.coding.core.prompt_templates import (
    COMPACTION_SUMMARY_TEMPLATE,
    CUSTOM_INSTRUCTIONS_SECTION,
    ENVIRONMENT_SECTION,
    FILE_CONTEXT_SECTION,
    SESSION_RESUME_TEMPLATE,
    SYSTEM_PROMPT_HEADER,
    TOOL_DESCRIPTION_TEMPLATE,
    TOOL_SECTION_HEADER,
)


def test_prompt_templates():
    assert "interactive coding agent" in SYSTEM_PROMPT_HEADER

    env = ENVIRONMENT_SECTION.format(
        working_dir="/test",
        os_name="Linux",
        os_version="1.0",
        python_version="3.11",
        shell="/bin/bash",
        date="2023-01-01"
    )
    assert "/test" in env
    assert "Linux" in env

    assert "Available Tools" in TOOL_SECTION_HEADER

    tool = TOOL_DESCRIPTION_TEMPLATE.format(
        name="test_tool",
        description="does things",
        parameters="none"
    )
    assert "### test_tool" in tool
    assert "does things" in tool

    assert "Custom Instructions" in CUSTOM_INSTRUCTIONS_SECTION.format(instructions="do not fail")

    files = FILE_CONTEXT_SECTION.format(file_entries="file1.txt\nfile2.txt")
    assert "file1.txt" in files

    summary = COMPACTION_SUMMARY_TEMPLATE.format(summary="we did some things")
    assert "we did some things" in summary
    assert "<conversation_summary>" in summary

    resume = SESSION_RESUME_TEMPLATE.format(summary="resume summary")
    assert "resume summary" in resume
