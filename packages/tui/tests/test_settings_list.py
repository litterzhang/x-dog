"""Tests for tui.components.settings_list — settings list component."""

from xdog.tui.components.settings_list import SettingItem, SettingsList, SettingsListTheme
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def _make_items() -> list[SettingItem]:
    return [
        SettingItem(id="theme", label="Theme", current_value="dark", values=["dark", "light", "auto"]),
        SettingItem(id="font", label="Font Size", current_value="14", values=["12", "14", "16"]),
        SettingItem(id="lang", label="Language", current_value="en", description="UI language"),
    ]

def _make_list(items=None, **kwargs) -> tuple[SettingsList, list[tuple[str, str]]]:
    changes: list[tuple[str, str]] = []
    cancelled: list[bool] = [False]

    sl = SettingsList(
        items=items or _make_items(),
        max_visible=10,
        theme=SettingsListTheme(),
        on_change=lambda sid, val: changes.append((sid, val)),
        on_cancel=lambda: cancelled.append(True),
        **kwargs,
    )
    return sl, changes

def test_settings_list_navigation():
    """Arrow keys move selected index."""
    sl, _ = _make_list()
    assert sl._selected_index == 0

    sl.handle_input(KeyEvent(key="down"))
    assert sl._selected_index == 1

    sl.handle_input(KeyEvent(key="down"))
    assert sl._selected_index == 2

    # Wraps around
    sl.handle_input(KeyEvent(key="down"))
    assert sl._selected_index == 0

    sl.handle_input(KeyEvent(key="up"))
    assert sl._selected_index == 2

def test_settings_list_value_cycling():
    """Enter/Space cycles through values list."""
    sl, changes = _make_list()
    # Item 0 is "Theme" with values ["dark", "light", "auto"]
    assert sl._items[0].current_value == "dark"

    sl.handle_input(KeyEvent(key="enter"))
    assert sl._items[0].current_value == "light"
    assert changes == [("theme", "light")]

    sl.handle_input(KeyEvent(key="enter"))
    assert sl._items[0].current_value == "auto"

    sl.handle_input(KeyEvent(key="enter"))
    assert sl._items[0].current_value == "dark"  # wraps

def test_settings_list_search():
    """Fuzzy search filters items."""
    sl, _ = _make_list(enable_search=True)
    lines = sl.render(60)
    text = "\n".join(strip_ansi(line) for line in lines)
    assert "Search:" in text

    # Type "fon" to filter
    sl.handle_input(KeyEvent(key="f"))
    sl.handle_input(KeyEvent(key="o"))
    sl.handle_input(KeyEvent(key="n"))

    assert len(sl._filtered_items) == 1
    assert sl._filtered_items[0].id == "font"
