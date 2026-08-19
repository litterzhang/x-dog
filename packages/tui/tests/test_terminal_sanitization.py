"""Security boundaries for rendering untrusted terminal text."""

from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text


def test_sanitize_terminal_text_removes_ansi_osc_and_controls() -> None:
    value = "safe\x1b[2J\x1b]52;c;clipboard\x07\x00\nnext\titem"

    assert sanitize_terminal_text(value) == "safe\nnext\titem"


def test_redact_sensitive_text_handles_inline_command_secrets() -> None:
    value = "curl -H 'Authorization: Bearer abc123' https://example api_key=xyz987"

    redacted = redact_sensitive_text(value)
    assert "abc123" not in redacted
    assert "xyz987" not in redacted
    assert redacted.count("<redacted>") == 2
