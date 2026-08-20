"""Application-owned viewport for optional fullscreen TUIs."""

from __future__ import annotations

from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component


class ScrollView(Component):
    def __init__(self, child: Component, *, height: int = 24, overscan: int = 4) -> None:
        self.child = child
        self.height = max(1, height)
        self.overscan = max(0, overscan)
        self.offset = 0
        self.follow_end = True
        self.query = ""

    def scroll(self, delta: int) -> None:
        self.follow_end = False
        self.offset = max(0, self.offset + delta)

    def scroll_to_end(self) -> None:
        self.follow_end = True

    def search(self, query: str, width: int) -> list[int]:
        if not query:
            return []
        return [
            index
            for index, line in enumerate(self.child.render(width))
            if query.casefold() in line.casefold()
        ]

    def select(self, start: int, end: int, width: int) -> str:
        lines = self.child.render(width)
        lo, hi = sorted((max(0, start), max(0, end)))
        return "\n".join(lines[lo:hi + 1])

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key == "pageup":
            self.scroll(-self.height)
            return True
        if event.key == "pagedown":
            self.scroll(self.height)
            return True
        if event.key == "end":
            self.scroll_to_end()
            return True
        return False

    def render(self, width: int) -> list[str]:
        lines = self.child.render(width)
        maximum = max(0, len(lines) - self.height)
        start = maximum if self.follow_end else min(self.offset, maximum)
        window_start = max(0, start - self.overscan)
        window_end = min(len(lines), start + self.height + self.overscan)
        window = lines[window_start:window_end]
        visible = window[start - window_start:start - window_start + self.height]
        if self.query:
            visible = [
                line.replace(self.query, f"\x1b[7m{self.query}\x1b[27m")
                for line in visible
            ]
        if len(lines) > self.height:
            thumb = min(self.height - 1, int(start / max(1, maximum) * (self.height - 1)))
            visible = [
                (line[: max(0, width - 1)] + ("█" if index == thumb else "│"))
                for index, line in enumerate(visible)
            ]
        return visible
