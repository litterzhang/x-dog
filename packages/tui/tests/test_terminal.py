
from xdog.tui.terminal import ScreenBuffer


def test_screen_buffer():
    buf = ScreenBuffer(10, 5)

    # write_text returns the next column
    c = buf.write_text(0, 0, "hello")
    assert c == 5

    cell = buf.get(0, 0)
    assert cell.char == "h"

    cell2 = buf.get(0, 4)
    assert cell2.char == "o"

