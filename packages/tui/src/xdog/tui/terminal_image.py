"""Terminal image rendering -- Kitty and iTerm2 inline image protocols.

Detects terminal capabilities and encodes image data for inline display
using the Kitty Graphics Protocol or iTerm2 Inline Images Protocol.

Also provides pure-Python parsers for PNG, JPEG, GIF, and WebP image
dimensions (no Pillow dependency).
"""

from __future__ import annotations

import base64
import math
import os
import random
import struct
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ImageProtocol = Literal["kitty", "iterm2"] | None


@dataclass(frozen=True)
class TerminalCapabilities:
    """Detected terminal image capabilities."""

    images: ImageProtocol = None
    true_color: bool = False
    hyperlinks: bool = True


@dataclass(frozen=True)
class CellDimensions:
    """Terminal cell size in pixels."""

    width_px: int = 9
    height_px: int = 18


@dataclass(frozen=True)
class ImageDimensions:
    """Image size in pixels."""

    width_px: int = 0
    height_px: int = 0


@dataclass(frozen=True)
class ImageRenderOptions:
    """Options for rendering an image in the terminal."""

    max_width_cells: int = 80
    max_height_cells: int | None = None
    preserve_aspect_ratio: bool = True
    image_id: int | None = None


# ---------------------------------------------------------------------------
# Capability detection (cached)
# ---------------------------------------------------------------------------

_cached_capabilities: TerminalCapabilities | None = None
_cell_dims = CellDimensions()


def get_cell_dimensions() -> CellDimensions:
    """Return the current cell dimensions."""
    return _cell_dims


def set_cell_dimensions(dims: CellDimensions) -> None:
    """Update cell dimensions (called by TUI when terminal responds to query)."""
    global _cell_dims
    _cell_dims = dims


def detect_capabilities() -> TerminalCapabilities:
    """Detect terminal image protocol support from environment variables."""
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    term = os.environ.get("TERM", "").lower()
    color_term = os.environ.get("COLORTERM", "").lower()

    # Kitty
    if os.environ.get("KITTY_WINDOW_ID") or term_program == "kitty":
        return TerminalCapabilities(images="kitty", true_color=True)

    # Ghostty (supports Kitty protocol)
    if term_program == "ghostty" or "ghostty" in term or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return TerminalCapabilities(images="kitty", true_color=True)

    # WezTerm (supports Kitty protocol)
    if os.environ.get("WEZTERM_PANE") or term_program == "wezterm":
        return TerminalCapabilities(images="kitty", true_color=True)

    # iTerm2
    if os.environ.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return TerminalCapabilities(images="iterm2", true_color=True)

    # VS Code terminal
    if term_program == "vscode":
        return TerminalCapabilities(true_color=True)

    # Alacritty
    if term_program == "alacritty":
        return TerminalCapabilities(true_color=True)

    true_color = color_term in ("truecolor", "24bit")
    return TerminalCapabilities(true_color=true_color)


def get_capabilities() -> TerminalCapabilities:
    """Return cached terminal capabilities (detects on first call)."""
    global _cached_capabilities
    if _cached_capabilities is None:
        _cached_capabilities = detect_capabilities()
    return _cached_capabilities


def reset_capabilities_cache() -> None:
    """Clear the cached capabilities so the next call re-detects."""
    global _cached_capabilities
    _cached_capabilities = None


# ---------------------------------------------------------------------------
# Image line detection
# ---------------------------------------------------------------------------

_KITTY_PREFIX = "\x1b_G"
_ITERM2_PREFIX = "\x1b]1337;File="


def is_image_line(line: str) -> bool:
    """Return *True* if *line* contains a Kitty or iTerm2 image sequence."""
    return _KITTY_PREFIX in line or _ITERM2_PREFIX in line


# ---------------------------------------------------------------------------
# Image ID management
# ---------------------------------------------------------------------------

def allocate_image_id() -> int:
    """Generate a random image ID for Kitty graphics protocol."""
    return random.randint(1, 0xFFFFFFFE)


# ---------------------------------------------------------------------------
# Kitty graphics protocol
# ---------------------------------------------------------------------------

def encode_kitty(
    base64_data: str,
    *,
    columns: int | None = None,
    rows: int | None = None,
    image_id: int | None = None,
) -> str:
    """Encode image data as a Kitty graphics protocol sequence.

    Parameters
    ----------
    base64_data:
        Base64-encoded image data.
    columns, rows:
        Target display size in terminal cells.
    image_id:
        Optional Kitty image ID for reuse / replacement.
    """
    chunk_size = 4096

    params: list[str] = ["a=T", "f=100", "q=2"]
    if columns is not None:
        params.append(f"c={columns}")
    if rows is not None:
        params.append(f"r={rows}")
    if image_id is not None:
        params.append(f"i={image_id}")

    param_str = ",".join(params)

    if len(base64_data) <= chunk_size:
        return f"\x1b_G{param_str};{base64_data}\x1b\\"

    chunks: list[str] = []
    offset = 0
    first = True

    while offset < len(base64_data):
        chunk = base64_data[offset : offset + chunk_size]
        is_last = offset + chunk_size >= len(base64_data)

        if first:
            chunks.append(f"\x1b_G{param_str},m=1;{chunk}\x1b\\")
            first = False
        elif is_last:
            chunks.append(f"\x1b_Gm=0;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm=1;{chunk}\x1b\\")

        offset += chunk_size

    return "".join(chunks)


def delete_kitty_image(image_id: int) -> str:
    """Return escape sequence to delete a Kitty image by ID."""
    return f"\x1b_Ga=d,d=I,i={image_id}\x1b\\"


def delete_all_kitty_images() -> str:
    """Return escape sequence to delete all visible Kitty images."""
    return "\x1b_Ga=d,d=A\x1b\\"


# ---------------------------------------------------------------------------
# iTerm2 inline images protocol
# ---------------------------------------------------------------------------

def encode_iterm2(
    base64_data: str,
    *,
    width: int | str | None = None,
    height: int | str | None = None,
    name: str | None = None,
    preserve_aspect_ratio: bool = True,
    inline: bool = True,
) -> str:
    """Encode image data as an iTerm2 inline image sequence."""
    params: list[str] = [f"inline={1 if inline else 0}"]

    if width is not None:
        params.append(f"width={width}")
    if height is not None:
        params.append(f"height={height}")
    if name is not None:
        name_b64 = base64.b64encode(name.encode()).decode()
        params.append(f"name={name_b64}")
    if not preserve_aspect_ratio:
        params.append("preserveAspectRatio=0")

    return f"\x1b]1337;File={';'.join(params)}:{base64_data}\x07"


# ---------------------------------------------------------------------------
# Image row calculation
# ---------------------------------------------------------------------------

def calculate_image_rows(
    image_dims: ImageDimensions,
    target_width_cells: int,
    cell_dims: CellDimensions | None = None,
) -> int:
    """Calculate the number of terminal rows an image will occupy."""
    if cell_dims is None:
        cell_dims = get_cell_dimensions()

    target_width_px = target_width_cells * cell_dims.width_px
    if image_dims.width_px == 0:
        return 1
    scale = target_width_px / image_dims.width_px
    scaled_height_px = image_dims.height_px * scale
    rows = math.ceil(scaled_height_px / cell_dims.height_px)
    return max(1, rows)


# ---------------------------------------------------------------------------
# Image dimension parsers (pure Python, no Pillow)
# ---------------------------------------------------------------------------

def get_png_dimensions(data: bytes) -> ImageDimensions | None:
    """Extract width/height from PNG header."""
    if len(data) < 24:
        return None
    # PNG signature: 0x89 P N G
    if data[:4] != b"\x89PNG":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return ImageDimensions(width_px=width, height_px=height)


def get_jpeg_dimensions(data: bytes) -> ImageDimensions | None:
    """Extract width/height from JPEG SOF marker."""
    if len(data) < 2 or data[0:2] != b"\xff\xd8":
        return None

    offset = 2
    while offset < len(data) - 9:
        if data[offset] != 0xFF:
            offset += 1
            continue

        marker = data[offset + 1]

        # SOF0, SOF1, SOF2
        if 0xC0 <= marker <= 0xC2:
            height = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            width = struct.unpack(">H", data[offset + 7 : offset + 9])[0]
            return ImageDimensions(width_px=width, height_px=height)

        if offset + 3 >= len(data):
            return None
        length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        if length < 2:
            return None
        offset += 2 + length

    return None


def get_gif_dimensions(data: bytes) -> ImageDimensions | None:
    """Extract width/height from GIF header."""
    if len(data) < 10:
        return None
    sig = data[:6]
    if sig not in (b"GIF87a", b"GIF89a"):
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return ImageDimensions(width_px=width, height_px=height)


def get_webp_dimensions(data: bytes) -> ImageDimensions | None:
    """Extract width/height from WebP header."""
    if len(data) < 30:
        return None
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None

    chunk = data[12:16]
    if chunk == b"VP8 ":
        if len(data) < 30:
            return None
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return ImageDimensions(width_px=width, height_px=height)
    elif chunk == b"VP8L":
        if len(data) < 25:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return ImageDimensions(width_px=width, height_px=height)
    elif chunk == b"VP8X":
        if len(data) < 30:
            return None
        width = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
        height = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
        return ImageDimensions(width_px=width, height_px=height)

    return None


def get_image_dimensions(data: bytes) -> ImageDimensions | None:
    """Auto-detect format and extract image dimensions from raw bytes."""
    # Try each format in order
    result = get_png_dimensions(data)
    if result is not None:
        return result
    result = get_jpeg_dimensions(data)
    if result is not None:
        return result
    result = get_gif_dimensions(data)
    if result is not None:
        return result
    return get_webp_dimensions(data)


# ---------------------------------------------------------------------------
# High-level render
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageRenderResult:
    """Result of rendering an image."""

    sequence: str
    rows: int
    image_id: int | None = None


def render_image(
    data: bytes,
    *,
    options: ImageRenderOptions | None = None,
) -> ImageRenderResult | None:
    """Render image data for the current terminal.

    Returns *None* if no image protocol is available.

    Parameters
    ----------
    data:
        Raw image bytes (PNG, JPEG, GIF, or WebP).
    options:
        Rendering options (width, height, image_id).
    """
    caps = get_capabilities()
    if caps.images is None:
        return None

    if options is None:
        options = ImageRenderOptions()

    dims = get_image_dimensions(data)
    if dims is None:
        dims = ImageDimensions(width_px=800, height_px=600)

    max_width = options.max_width_cells
    rows = calculate_image_rows(dims, max_width)

    b64 = base64.b64encode(data).decode("ascii")

    if caps.images == "kitty":
        sequence = encode_kitty(b64, columns=max_width, rows=rows, image_id=options.image_id)
        return ImageRenderResult(sequence=sequence, rows=rows, image_id=options.image_id)

    if caps.images == "iterm2":
        sequence = encode_iterm2(
            b64,
            width=max_width,
            height="auto",
            preserve_aspect_ratio=options.preserve_aspect_ratio,
        )
        return ImageRenderResult(sequence=sequence, rows=rows)

    return None


def image_fallback(
    alt: str = "image",
    dimensions: ImageDimensions | None = None,
    filename: str | None = None,
) -> str:
    """Return a text fallback string for an image."""
    parts: list[str] = []
    if filename:
        parts.append(filename)
    parts.append(f"[{alt}]")
    if dimensions:
        parts.append(f"{dimensions.width_px}x{dimensions.height_px}")
    return f"[Image: {' '.join(parts)}]"
