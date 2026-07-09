
from pathlib import Path
from claw.core.memory.long_term import LongTermMemory

def test_append_adds_to_existing(tmp_path):
    ltm = LongTermMemory(tmp_path)
    ltm.write("hello")
    ltm.append("world")
    assert "hello" in ltm.read()
    assert "world" in ltm.read()

def test_update_section_replaces_content(tmp_path):
    ltm = LongTermMemory(tmp_path)
    ltm.write("# Section 1\nold content\n# Section 2\nother\n")
    ltm.update_section("Section 1", "new content")
    content = ltm.read()
    assert "new content" in content
    assert "old content" not in content
    assert "# Section 2" in content
