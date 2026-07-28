import time

from coding.core.timings import TimingCollector


def test_timing_collector_measure():
    collector = TimingCollector(enabled=True)
    with collector.measure("test_block"):
        time.sleep(0.01)

    entries = collector.get_entries()
    assert len(entries) == 1
    assert entries[0].label == "test_block"
    assert entries[0].elapsed_ms >= 10.0
