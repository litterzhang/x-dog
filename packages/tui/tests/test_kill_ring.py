from xdog.tui.kill_ring import KillRing


def test_kill_ring_yank_pop():
    ring = KillRing()
    ring.kill("first")
    ring.kill("second")
    ring.kill("third")

    assert ring.yank() == "third"
    assert ring.yank_pop() == "second"
    assert ring.yank_pop() == "first"
    assert ring.yank_pop() == "third"  # Wraps around

def test_kill_ring_append():
    ring = KillRing()
    ring.kill("hello")
    ring.append_kill(" world")

    assert ring.yank() == "hello world"
    assert ring.size == 1

