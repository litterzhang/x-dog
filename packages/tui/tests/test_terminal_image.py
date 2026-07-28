"""Tests for tui.terminal_image — image dimension parsing and encoding."""

import struct

from tui.terminal_image import (
    encode_iterm2,
    encode_kitty,
    get_gif_dimensions,
    get_png_dimensions,
)


def _make_png(width: int = 100, height: int = 200) -> bytes:
    """Create minimal PNG header bytes."""
    header = b"\x89PNG\r\n\x1a\n"
    # Pad to offset 16 for width/height
    padding = b"\x00" * (16 - len(header))
    dims = struct.pack(">II", width, height)
    return header + padding + dims + b"\x00" * 10

def _make_gif(width: int = 320, height: int = 240) -> bytes:
    """Create minimal GIF header bytes."""
    header = b"GIF89a"
    dims = struct.pack("<HH", width, height)
    return header + dims + b"\x00" * 10

def test_png_dimensions():
    data = _make_png(800, 600)
    dims = get_png_dimensions(data)
    assert dims is not None
    assert dims.width_px == 800
    assert dims.height_px == 600

def test_gif_dimensions():
    data = _make_gif(320, 240)
    dims = get_gif_dimensions(data)
    assert dims is not None
    assert dims.width_px == 320
    assert dims.height_px == 240

def test_encode_kitty_short():
    """Short data produces single chunk."""
    result = encode_kitty("AAAA", columns=10, rows=5)
    assert result.startswith("\x1b_G")
    assert "AAAA" in result
    assert "c=10" in result
    assert "r=5" in result

def test_encode_kitty_chunked():
    """Long data produces multiple chunks."""
    data = "A" * 5000
    result = encode_kitty(data)
    assert result.count("\x1b_G") >= 2  # At least 2 chunks
    assert "m=1" in result  # Continuation marker

def test_encode_iterm2():
    result = encode_iterm2("AAAA", width=80, height="auto")
    assert result.startswith("\x1b]1337;File=")
    assert "width=80" in result
    assert "AAAA" in result
