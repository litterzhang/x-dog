from tui.keybindings import KeybindingManager, KeybindingScope
from tui.keys import KeyEvent


def test_keybinding_scope():
    scope = KeybindingScope("global")
    scope.add("ctrl+c", "copy")
    scope.add("ctrl+v", "paste")

    assert scope.match(KeyEvent(key="c", ctrl=True)).action == "copy"
    assert scope.match(KeyEvent(key="v", ctrl=True)).action == "paste"
    assert scope.match(KeyEvent(key="x", ctrl=True)) is None

def test_keybinding_manager():
    mgr = KeybindingManager()

    scope1 = mgr.create_scope("global")
    scope1.add("escape", "cancel")
    scope1.add("enter", "submit")

    scope2 = mgr.create_scope("dialog")
    scope2.add("escape", "close_dialog")

    # scope2 should take precedence
    assert mgr.match(KeyEvent(key="escape")).action == "close_dialog"
    assert mgr.match(KeyEvent(key="enter")).action == "submit"

    mgr.disable_scope("dialog")
    assert mgr.match(KeyEvent(key="escape")).action == "cancel"

