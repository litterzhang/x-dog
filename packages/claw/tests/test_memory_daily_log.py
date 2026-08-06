
from xdog.claw.core.memory.daily_log import DailyLog


def test_append_adds_timestamped_entry(tmp_path):
    log = DailyLog(tmp_path)
    log.append("test entry", date_str="2024-01-01")
    content = log.read_date("2024-01-01")
    assert "test entry" in content
    assert "[" in content and "]" in content
