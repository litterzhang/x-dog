"""Tests for interactive mode components."""


from coding.modes.interactive.theme import (
    create_default_theme,
)


class TestFooterComponent:
    """Tests for the footer component."""

class TestToolExecutionComponent:
    """Tests for the tool execution component."""

    def test_summarize_args_bash(self):
        from coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"command": "echo hello"}, "bash")
        assert result == "echo hello"

    def test_summarize_args_filesystem(self):
        from coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"action": "read", "path": "/test.py"}, "filesystem")
        assert result == "read /test.py"

    def test_summarize_args_grep(self):
        from coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"pattern": "foo", "path": "."}, "grep")
        assert result == "/foo/ in ."

class TestChatLog:
    """Tests for the chat log component."""

    def test_chat_log_streaming(self):
        from coding.modes.interactive.components.chat_log import ChatLog
        theme = create_default_theme()
        log = ChatLog(theme)
        log.start_assistant("Hel", "stream1")
        log.update_assistant("Hello wo", "stream1")
        log.finalize_assistant("Hello world!", "stream1")
        lines = log.render(80)
        assert len(lines) > 0

    def test_chat_log_pruning(self):
        from coding.modes.interactive.components.chat_log import ChatLog
        theme = create_default_theme()
        log = ChatLog(theme)
        for i in range(250):
            log.add_system(f"msg {i}")
        assert len(log.children) <= ChatLog.MAX_COMPONENTS

class TestCustomEditor:
    """Tests for the custom editor component."""

    def test_editor_text_operations(self):
        from coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.set_text("hello")
        assert editor.get_text() == "hello"

    def test_editor_history(self):
        from coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.add_to_history("first")
        editor.add_to_history("second")
        assert len(editor._history) == 2
        # Duplicate should not be added
        editor.add_to_history("second")
        assert len(editor._history) == 2

class TestSlashSelectList:
    """Tests for the slash command select list."""

    def test_select_list(self):
        from coding.modes.interactive.components.custom_editor import SlashSelectList
        items = [("/help", "Show help"), ("/model", "Show model")]
        sl = SlashSelectList(items)
        assert sl.selected_value == "/help"
        sl.move(1)
        assert sl.selected_value == "/model"
        sl.move(1)  # wraps around
        assert sl.selected_value == "/help"

class TestDiffComponent:
    """Tests for the Diff component with custom diff format."""

    def test_diff_context_lines(self):
        from tui.components.diff import Diff
        diff_text = " 1 context line"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) >= 1
        assert "context line" in lines[0]

    def test_diff_intra_line_highlight(self):
        """When 1 removed + 1 added, intra-line diff applies inverse."""
        from tui.components.diff import Diff
        diff_text = "-3 hello world\n+3 hello earth"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) == 2
        # Inverse ANSI should appear for the changed word
        assert "\x1b[7m" in lines[0] or "\x1b[7m" in lines[1]

class TestToolExecutionState:
    """Tests for tool execution state tracking."""

    def test_expand_collapse_large_output(self):
        from coding.modes.interactive.components.tool_execution import ToolExecutionComponent
        theme = create_default_theme()
        comp = ToolExecutionComponent("bash", None, theme)
        # Generate large output (>500 chars)
        big_output = "x" * 600
        comp.set_result(big_output)
        lines = comp.render(80)
        rendered = " ".join(lines)
        # Should show collapsed summary
        assert "more chars" in rendered

class TestToolExecutionDiffDetection:
    """Tests for diff detection in tool_execution.py."""

    def test_extract_diff(self):
        from coding.modes.interactive.components.tool_execution import _extract_diff
        text = "Successfully replaced 1 occurrence\n\n-1 old\n+1 new"
        diff = _extract_diff(text)
        assert diff.startswith("-1 old")

class TestInitialMessage:
    """Tests for the initial message builder."""

    def test_build_with_prompt(self):
        from coding.cli.initial_message import build_initial_message
        msg = build_initial_message(prompt="hello")
        assert msg == "hello"

    def test_build_with_prompt_and_files(self, tmp_path):
        from coding.cli.initial_message import build_initial_message
        f = tmp_path / "test.txt"
        f.write_text("file content", encoding="utf-8")
        msg = build_initial_message(prompt="analyze this", files=(f,))
        assert msg is not None
        assert "file content" in msg
        assert "analyze this" in msg
