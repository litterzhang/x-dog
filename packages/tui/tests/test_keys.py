from xdog.tui.keys import (
    KeyEvent,
    KeyEventType,
    is_key_release,
    is_key_repeat,
    parse_key_events,
)


def test_escape():
    events = parse_key_events(b'\x1b')
    assert len(events) == 1
    assert events[0].matches("escape")

def test_arrows():
    events = parse_key_events(b'\x1b[A')
    assert len(events) == 1
    assert events[0].matches("up")

    events = parse_key_events(b'\x1b[B')
    assert len(events) == 1
    assert events[0].matches("down")

def test_multiple_keys():
    events = parse_key_events(b'ab\x03')
    assert len(events) == 3
    assert events[0].matches("a")
    assert events[1].matches("b")
    assert events[2].matches("ctrl+c")

def test_tilde_sequences():
    events = parse_key_events(b'\x1b[15~')
    assert len(events) == 1
    assert events[0].matches("f5")

    events = parse_key_events(b'\x1b[1;2A')
    assert len(events) == 1
    assert events[0].matches("shift+up")

def test_backtab_csi_z():
    """CSI Z is Shift+Tab (backtab) on legacy xterm-style terminals."""
    events = parse_key_events(b'\x1b[Z')
    assert len(events) == 1
    assert events[0].key == "tab"
    assert events[0].shift is True
    assert events[0].matches("shift+tab")

def test_modify_other_keys_ctrl_enter():
    events = parse_key_events(b"\x1b[27;5;13~")
    assert events == [KeyEvent(key="enter", ctrl=True)]


# ---------- Kitty keyboard protocol tests ----------


def test_kitty_simple_letter():
    """CSI 97 u  →  'a' key press (Kitty protocol)."""
    events = parse_key_events(b"\x1b[97u")
    assert len(events) == 1
    assert events[0].key == "a"
    assert events[0].event_type == KeyEventType.PRESS

def test_kitty_shifted_alternate_codepoint():
    events = parse_key_events(b"\x1b[97:65;2u")
    assert events == [KeyEvent(key="A", shift=True)]


def test_kitty_release_event():
    """CSI 97;1:3u  →  'a' key release (event type 3)."""
    events = parse_key_events(b"\x1b[97;1:3u")
    assert len(events) == 1
    assert events[0].key == "a"
    assert events[0].event_type == KeyEventType.RELEASE
    assert is_key_release(events[0])
    assert not is_key_repeat(events[0])

