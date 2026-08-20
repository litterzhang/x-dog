"""Main-buffer terminal input protocol negotiation and cleanup."""

from __future__ import annotations

import re

from xdog.tui.keys import set_kitty_protocol_active

_KITTY_RESPONSE = re.compile(rb"^\x1b\[\?(\d+)u")
_DA_RESPONSE = re.compile(rb"^\x1b\[\?[0-9;]*c")


class TerminalProtocol:
    def __init__(self) -> None:
        self._kitty_pushed = False
        self._modify_other_keys = False
        self._pending = ""
        self._negotiating = False
        self._input = bytearray()

    def startup(self) -> str:
        self._kitty_pushed = True
        self._negotiating = True
        set_kitty_protocol_active(False)
        return "\x1b[?2004h\x1b[>7u\x1b[?u\x1b[c"

    def filter_input(self, data: bytes) -> bytes:
        # Kitty and DA replies may be fragmented, delayed, or combined in the
        # same read. Consume every leading protocol reply before returning user
        # bytes to the editor.
        candidate = bytes(self._input) + data
        self._input.clear()

        while True:
            kitty = _KITTY_RESPONSE.match(candidate)
            if kitty:
                flags = int(kitty.group(1))
                self._negotiating = False
                if flags:
                    set_kitty_protocol_active(True)
                else:
                    self._enable_fallback()
                candidate = candidate[kitty.end():]
                continue

            da = _DA_RESPONSE.match(candidate)
            if da:
                if self._negotiating:
                    self._negotiating = False
                    self._enable_fallback()
                candidate = candidate[da.end():]
                continue
            break

        if candidate.startswith(b"\x1b[?") and self._negotiating:
            self._input.extend(candidate)
            return b""
        return candidate

    def _enable_fallback(self) -> None:
        self._modify_other_keys = True
        self._pending += "\x1b[>4;2m"

    def pending_output(self) -> str:
        output = self._pending
        self._pending = ""
        return output

    def cleanup(self) -> str:
        output = "\x1b[?2004l"
        if self._kitty_pushed:
            output += "\x1b[<u"
        if self._modify_other_keys:
            output += "\x1b[>4;0m"
        self._kitty_pushed = False
        self._modify_other_keys = False
        self._negotiating = False
        self._pending = ""
        set_kitty_protocol_active(False)
        return output
