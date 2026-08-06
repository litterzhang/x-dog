from xdog.tui.stdin_buffer import is_complete_sequence


def test_complete_csi_sequence():
    """CSI sequence with final byte is complete."""
    assert is_complete_sequence(b"\x1b[A") is True  # Up arrow
    assert is_complete_sequence(b"\x1b[1;2A") is True  # Shift+Up
    assert is_complete_sequence(b"\x1b[15~") is True  # F5

def test_incomplete_csi_sequence():
    """CSI sequence without final byte is incomplete."""
    assert is_complete_sequence(b"\x1b[") is False
    assert is_complete_sequence(b"\x1b[1;2") is False

def test_bare_escape_incomplete():
    """Bare ESC at end of buffer is incomplete."""
    assert is_complete_sequence(b"\x1b") is False

def test_mixed_complete():
    """Mix of regular bytes and complete sequences is complete."""
    assert is_complete_sequence(b"hello\x1b[A") is True
