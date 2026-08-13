"""Tests for interactive mode components."""


from xdog.coding.modes.interactive.theme import (
    create_default_theme,
)


class TestFooterComponent:
    """Tests for the footer component."""


class TestAssistantMessageComponent:
    def test_renders_reasoning_and_response(self):
        from xdog.coding.modes.interactive.components.assistant_message import (
            AssistantMessageComponent,
        )
        from xdog.tui.utils import strip_ansi

        component = AssistantMessageComponent(
            "final answer",
            create_default_theme(),
            thinking="reasoning details",
        )
        rendered = "\n".join(strip_ansi(line) for line in component.render(80))

        assert "Thinking" in rendered
        assert "reasoning details" in rendered
        assert "final answer" in rendered


class TestPermissionPromptComponent:
    def test_allow_once_and_escape_to_deny(self):
        from xdog.coding.core.permissions import PermissionRequest
        from xdog.coding.modes.interactive.components.permission_prompt import (
            PermissionPromptComponent,
        )
        from xdog.tui.keys import KeyEvent
        from xdog.tui.utils import strip_ansi

        decisions: list[str] = []
        request = PermissionRequest(
            id="req-1",
            tool_name="bash",
            arguments={"command": "pytest"},
            summary="Run shell command:\npytest",
        )
        prompt = PermissionPromptComponent(
            request,
            create_default_theme(),
            decisions.append,  # type: ignore[arg-type]
        )

        rendered = "\n".join(strip_ansi(line) for line in prompt.render(80))
        assert '"command"' not in rendered

        assert prompt.handle_input(KeyEvent(key="enter"))
        assert decisions == ["allow_once"]

        decisions.clear()
        prompt = PermissionPromptComponent(
            request,
            create_default_theme(),
            decisions.append,  # type: ignore[arg-type]
        )
        assert prompt.handle_input(KeyEvent(key="escape"))
        assert decisions == ["deny"]

    def test_permission_panel_is_after_editor(self):
        from unittest.mock import MagicMock

        from xdog.coding.core.permissions import PermissionRequest
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)
        root = mode._tui.children[0]

        assert root.children[-2] is mode._editor
        assert root.children[-1] is mode._permission_container
        assert mode._permission_container.children == []

        mode._show_permission_request(PermissionRequest(
            id="req-inline",
            tool_name="bash",
            arguments={"command": "pwd"},
            summary="Run shell command:\npwd",
        ))

        assert mode._permission_container.children == [mode._permission_prompt]
        assert mode._tui._focused is mode._permission_prompt


class TestToolExecutionComponent:
    """Tests for the tool execution component."""

    def test_summarize_args_bash(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"command": "echo hello"}, "bash")
        assert result == "echo hello"

    def test_summarize_args_filesystem(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"action": "read", "path": "/test.py"}, "filesystem")
        assert result == "read /test.py"


    def test_summarize_args_grep(self):
        from xdog.coding.modes.interactive.components.tool_execution import _summarize_args
        result = _summarize_args({"pattern": "foo", "path": "."}, "grep")
        assert result == "/foo/ in ."


class TestChatLog:
    """Tests for the chat log component."""

    def test_chat_log_streaming(self):
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        theme = create_default_theme()
        log = ChatLog(theme)
        log.start_assistant("Hel", "stream1")
        log.update_assistant("Hello wo", "stream1")
        log.finalize_assistant("Hello world!", "stream1")
        lines = log.render(80)
        assert len(lines) > 0

    def test_chat_log_pruning(self):
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        theme = create_default_theme()
        log = ChatLog(theme)
        for i in range(250):
            log.add_system(f"msg {i}")
        assert len(log.children) <= ChatLog.MAX_COMPONENTS


class TestInteractiveMessageHandling:
    def test_message_end_displays_non_streamed_text(self):
        import queue

        from xdog.agent import MessageEndEvent
        from xdog.ai.types import AssistantMessage, TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(MessageEndEvent(
            message=AssistantMessage(content=(TextContent(text="final text"),)),
        ))

        event = mode._event_queue.get_nowait()
        assert event["type"] == "text_update"
        assert event["text"] == "final text"

    def test_each_agent_turn_queues_footer_refresh(self):
        import queue

        from xdog.agent import TurnEndEvent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(TurnEndEvent())

        assert mode._event_queue.get_nowait() == {"type": "turn_footer_update"}

    def test_turn_footer_event_updates_footer_immediately(self):
        from unittest.mock import Mock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._update_footer = Mock()
        mode._update_header = Mock()
        mode._handle_ui_event({"type": "turn_footer_update"})

        mode._update_footer.assert_called_once_with()
        mode._update_header.assert_called_once_with()

    def test_busy_poll_does_not_request_redundant_render(self):
        import queue
        from unittest.mock import Mock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._is_busy = True
        mode._awaiting_permission = False
        mode._format_elapsed = Mock(return_value="0s")
        mode._status_loader = Mock()
        mode._last_busy_label = "thinking... • 0s"
        mode._tui = Mock()

        mode._poll()

        mode._status_loader.set_message.assert_not_called()
        mode._tui.request_render.assert_not_called()

    def test_post_tool_text_is_rendered_after_tool_result(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)

        mode._handle_ui_event({"type": "assistant_start"})
        mode._handle_ui_event({
            "type": "text_update",
            "text": "",
            "thinking": "planning",
        })
        mode._handle_ui_event({"type": "assistant_end"})
        mode._handle_ui_event({
            "type": "tool_call",
            "id": "call-1",
            "name": "bash",
            "arguments": {"command": "pwd"},
        })
        mode._handle_ui_event({
            "type": "tool_result",
            "id": "call-1",
            "name": "bash",
            "result": "/workspace",
            "is_error": False,
        })
        mode._handle_ui_event({"type": "assistant_start"})
        mode._handle_ui_event({
            "type": "text_update",
            "text": "latest answer",
            "thinking": "",
        })
        mode._handle_ui_event({"type": "assistant_end"})

        rendered = "\n".join(
            strip_ansi(line) for line in mode._chat_log.render(80)
        )
        assert rendered.index("/workspace") < rendered.index("latest answer")

    def test_tool_events_keep_their_call_ids(self):
        import queue

        from xdog.agent import (
            AgentToolResult,
            ToolExecutionEndEvent,
            ToolExecutionStartEvent,
        )
        from xdog.ai.types import TextContent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

        mode = object.__new__(InteractiveMode)
        mode._event_queue = queue.Queue()
        mode._dispatch_agent_event(ToolExecutionStartEvent(
            tool_call_id="call-a",
            tool_name="bash",
            args={"command": "pwd"},
        ))
        mode._dispatch_agent_event(ToolExecutionEndEvent(
            tool_call_id="call-a",
            tool_name="bash",
            result=AgentToolResult(content=(TextContent(text="done"),)),
        ))

        started = mode._event_queue.get_nowait()
        finished = mode._event_queue.get_nowait()
        assert started["id"] == "call-a"
        assert finished["id"] == "call-a"

    def test_parallel_tools_update_their_own_status_components(self):
        from unittest.mock import MagicMock

        from xdog.coding.modes.interactive.components.tool_execution import (
            ToolExecutionComponent,
        )
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        session = MagicMock()
        session.agent.subscribe.return_value = lambda: None
        session.permissions.mode = "ask"
        mode = InteractiveMode(session)

        for index in range(3):
            mode._handle_ui_event({
                "type": "tool_call",
                "id": f"call-{index}",
                "name": "bash",
                "arguments": {"command": f"echo {index}"},
            })

        # Parallel calls can finish in any order.
        for index in (1, 0, 2):
            mode._handle_ui_event({
                "type": "tool_result",
                "id": f"call-{index}",
                "name": "bash",
                "result": f"result-{index}",
                "is_error": False,
            })

        components = [
            child
            for child in mode._chat_log.children
            if isinstance(child, ToolExecutionComponent)
        ]
        assert len(components) == 3
        assert all(component._state == "success" for component in components)
        assert mode._tool_components == {}
        rendered = "\n".join(
            strip_ansi(line) for line in mode._chat_log.render(80)
        )
        assert all(f"result-{index}" in rendered for index in range(3))

    def test_replay_restores_tool_call_and_result(self):
        from types import SimpleNamespace

        from xdog.ai.types import (
            AssistantMessage,
            TextContent,
            ToolCall,
            ToolResultMessage,
        )
        from xdog.coding.modes.interactive.components.chat_log import ChatLog
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        from xdog.tui.utils import strip_ansi

        theme = create_default_theme()
        mode = object.__new__(InteractiveMode)
        mode._session = SimpleNamespace(messages=[
            AssistantMessage(content=(
                ToolCall(id="call-1", name="bash", arguments={"command": "pwd"}),
            )),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="bash",
                content=(TextContent(text="/workspace"),),
            ),
        ])
        mode._chat_log = ChatLog(theme)
        mode._editor = CustomEditorComponent(theme)

        mode._replay_history()
        rendered = "\n".join(strip_ansi(line) for line in mode._chat_log.render(80))

        assert "bash" in rendered
        assert "pwd" in rendered
        assert "/workspace" in rendered


class TestCustomEditor:
    """Tests for the custom editor component."""

    def test_editor_text_operations(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.set_text("hello")
        assert editor.get_text() == "hello"

    def test_editor_history(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        theme = create_default_theme()
        editor = CustomEditorComponent(theme)
        editor.add_to_history("first")
        editor.add_to_history("second")
        assert len(editor._history) == 2
        # Duplicate should not be added
        editor.add_to_history("second")
        assert len(editor._history) == 2

    def test_editor_wraps_long_input_to_terminal_width(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.utils import string_width, strip_ansi

        editor = CustomEditorComponent(create_default_theme())
        editor.set_text("abcdefghij")
        lines = editor.render(8)

        assert all(string_width(line) <= 8 for line in lines)
        visible = [strip_ansi(line) for line in lines]
        assert any("abcdef" in line for line in visible)
        assert any("ghij" in line for line in visible)

    def test_editor_supports_multiline_input(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.keys import KeyEvent

        editor = CustomEditorComponent(create_default_theme())
        submitted: list[str] = []
        editor.on_submit = submitted.append
        editor.set_text("first")

        assert editor.handle_input(KeyEvent(key="enter", alt=True))
        for ch in "second":
            assert editor.handle_input(KeyEvent(key=ch))

        assert editor.get_text() == "first\nsecond"
        assert editor.handle_input(KeyEvent(key="enter"))
        assert submitted == ["first\nsecond"]

    def test_editor_multiline_vertical_movement(self):
        from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
        from xdog.tui.keys import KeyEvent

        editor = CustomEditorComponent(create_default_theme())
        editor.set_text("abc\n12345")
        assert editor.handle_input(KeyEvent(key="up"))
        assert editor._cursor == 3
        assert editor.handle_input(KeyEvent(key="down"))
        assert editor._cursor == 7

class TestSlashSelectList:
    """Tests for the slash command select list."""

    def test_select_list(self):
        from xdog.coding.modes.interactive.components.custom_editor import SlashSelectList
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
        from xdog.tui.components.diff import Diff
        diff_text = " 1 context line"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) >= 1
        assert "context line" in lines[0]

    def test_diff_intra_line_highlight(self):
        """When 1 removed + 1 added, intra-line diff applies inverse."""
        from xdog.tui.components.diff import Diff
        diff_text = "-3 hello world\n+3 hello earth"
        d = Diff(diff_text, padding_left=0)
        lines = d.render(80)
        assert len(lines) == 2
        # Inverse ANSI should appear for the changed word
        assert "\x1b[7m" in lines[0] or "\x1b[7m" in lines[1]

class TestToolExecutionState:
    """Tests for tool execution state tracking."""

    def test_expand_collapse_large_output(self):
        from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
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
        from xdog.coding.modes.interactive.components.tool_execution import _extract_diff
        text = "Successfully replaced 1 occurrence\n\n-1 old\n+1 new"
        diff = _extract_diff(text)
        assert diff.startswith("-1 old")

class TestInitialMessage:
    """Tests for the initial message builder."""

    def test_build_with_prompt(self):
        from xdog.coding.cli.initial_message import build_initial_message
        msg = build_initial_message(prompt="hello")
        assert msg == "hello"

    def test_build_with_prompt_and_files(self, tmp_path):
        from xdog.coding.cli.initial_message import build_initial_message
        f = tmp_path / "test.txt"
        f.write_text("file content", encoding="utf-8")
        msg = build_initial_message(prompt="analyze this", files=(f,))
        assert msg is not None
        assert "file content" in msg
        assert "analyze this" in msg
