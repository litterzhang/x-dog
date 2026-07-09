import pytest
import asyncio
from coding.core.event_bus import EventBus, get_event_bus

@pytest.mark.asyncio
async def test_event_bus_emit_and_on():
    bus = EventBus()
    
    received = []
    
    async def handler(msg: str):
        received.append(msg)
        
    bus.on("test_event", handler)
    
    await bus.emit("test_event", msg="hello")
    assert received == ["hello"]
    
    bus.off("test_event", handler)
    await bus.emit("test_event", msg="ignored")
    assert received == ["hello"]  # unchanged
