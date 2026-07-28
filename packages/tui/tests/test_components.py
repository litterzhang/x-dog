
# ---------- Editor kill ring & undo tests ----------

from tui.components.editor import Editor
from tui.keys import KeyEvent


def test_editor_kill_to_eol():
    """Ctrl+K kills text from cursor to end of line."""
    editor = Editor()
    editor.focused = True
    for ch in "hello world":
        editor.handle_input(KeyEvent(key=ch))
    editor._cursor_col = 5
    editor.handle_input(KeyEvent(key="k", ctrl=True))
    assert editor.get_text() == "hello"

def test_editor_undo_redo():
    """Ctrl+Z undoes, Ctrl+Shift+Z redoes."""
    editor = Editor()
    editor.focused = True
    for ch in "ab":
        editor.handle_input(KeyEvent(key=ch))
    assert editor.get_text() == "ab"

    editor.handle_input(KeyEvent(key="z", ctrl=True))
    assert editor.get_text() == "a"

    editor.handle_input(KeyEvent(key="z", ctrl=True, shift=True))
    assert editor.get_text() == "ab"

def test_editor_yank():
    """Ctrl+K then Ctrl+Y yanks killed text."""
    editor = Editor()
    editor.focused = True
    for ch in "hello world":
        editor.handle_input(KeyEvent(key=ch))
    editor._cursor_col = 5
    editor.handle_input(KeyEvent(key="k", ctrl=True))
    assert editor.get_text() == "hello"

    editor._cursor_col = len("hello")
    editor.handle_input(KeyEvent(key="y", ctrl=True))
    assert editor.get_text() == "hello world"

# ---------- Image component tests ----------

