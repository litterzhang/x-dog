import pytest
from tui.utils import string_width, char_width

def test_regional_indicator_width():
    partial_flag = "🇨"
    assert string_width(partial_flag) == 2

def test_full_flags():
    samples = ["🇯🇵", "🇺🇸", "🇬🇧", "🇨🇳", "🇩🇪", "🇫🇷"]
    for flag in samples:
        w = string_width(flag)
        assert w == 2 or w == 4 # depending on wcwidth implementation

