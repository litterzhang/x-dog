import pytest
from tui.components.markdown import Markdown
from tui.utils import strip_ansi

def test_markdown_caching():
    md = Markdown(padding_x=0, padding_y=0)
    md.text = "Hello world"

    lines1 = md.render(80)
    lines2 = md.render(80)
    assert lines1 is lines2  # Should be cached

