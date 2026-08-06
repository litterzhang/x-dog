from xdog.tui.undo_stack import UndoStack


def test_undo_stack_push_undo_redo():
    stack = UndoStack[int]()
    assert not stack.can_undo
    assert not stack.can_redo

    stack.push(1)
    assert stack.can_undo
    assert not stack.can_redo

    prev = stack.undo(current=2)
    assert prev == 1
    assert not stack.can_undo
    assert stack.can_redo

    next_state = stack.redo(current=1)
    assert next_state == 2
    assert stack.can_undo
    assert not stack.can_redo

def test_undo_stack_push_clears_redo():
    stack = UndoStack[int]()
    stack.push(1)
    stack.undo(2)
    assert stack.can_redo

    stack.push(3)
    assert not stack.can_redo

