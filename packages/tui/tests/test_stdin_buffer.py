from xdog.tui.stdin_buffer import KeyBytes, Paste, StdinBuffer, is_complete_sequence


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


def test_feed_preserves_fragmented_csi_until_complete():
    buffer = StdinBuffer()

    assert buffer.feed(b"\x1b[") == []
    assert buffer.feed(b"1;2A") == [KeyBytes(b"\x1b[1;2A")]


def test_feed_emits_bracketed_paste_atomically():
    buffer = StdinBuffer()

    assert buffer.feed(b"before\x1b[200~first\n") == [KeyBytes(b"before")]
    assert buffer.feed("二\nthird".encode()) == []
    assert buffer.feed(b"\x1b[201~after") == [
        Paste("first\n二\nthird"),
        KeyBytes(b"after"),
    ]


def test_feed_keeps_split_utf8_character():
    buffer = StdinBuffer()
    encoded = "界".encode()

    assert buffer.feed(encoded[:2]) == []
    assert buffer.feed(encoded[2:]) == [KeyBytes(encoded)]
