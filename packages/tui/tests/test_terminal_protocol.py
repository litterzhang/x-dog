from xdog.tui.keys import is_kitty_protocol_active
from xdog.tui.terminal_protocol import TerminalProtocol


def test_protocol_starts_with_paste_and_kitty_query() -> None:
    protocol = TerminalProtocol()
    assert protocol.startup() == "\x1b[?2004h\x1b[>7u\x1b[?u\x1b[c"


def test_protocol_buffers_fragmented_kitty_response() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?") == b""
    assert protocol.filter_input(b"7u") == b""
    assert is_kitty_protocol_active()


def test_protocol_consumes_response_and_replays_trailing_input() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?7ua") == b"a"
    assert is_kitty_protocol_active()


def test_protocol_replays_false_negotiation_prefix() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"hello") == b"hello"


def test_protocol_consumes_combined_kitty_and_da_responses() -> None:
    protocol = TerminalProtocol()
    protocol.startup()

    assert protocol.filter_input(b"\x1b[?7u\x1b[?62;22c") == b""
    assert is_kitty_protocol_active()


def test_protocol_consumes_combined_replies_and_preserves_user_input() -> None:
    protocol = TerminalProtocol()
    protocol.startup()

    assert protocol.filter_input(b"\x1b[?7u\x1b[?62;22chello") == b"hello"
    assert is_kitty_protocol_active()


def test_protocol_consumes_delayed_da_after_kitty_response() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?7u") == b""
    assert is_kitty_protocol_active()

    assert protocol.filter_input(b"\x1b[?62;22c") == b""
    assert is_kitty_protocol_active()


def test_protocol_consumes_kitty_response() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?7u") == b""
    assert is_kitty_protocol_active()
    assert protocol.cleanup() == "\x1b[?2004l\x1b[<u"


def test_protocol_da_response_enables_modify_other_keys() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?1;2c") == b""
    assert protocol.pending_output() == "\x1b[>4;2m"
    assert protocol.cleanup() == "\x1b[?2004l\x1b[<u\x1b[>4;0m"
