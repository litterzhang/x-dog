import os

import pytest
from xdog.agent.agent import Agent
from xdog.ai import Tool


def get_calculate_tool():
    return Tool(
        name="calculate",
        description="Calculate a math expression",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to calculate"
                }
            },
            "required": ["expression"]
        },
        execute=lambda args: {"result": eval(args["expression"])},
    )

@pytest.mark.asyncio
async def test_e2e_basic():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    import xdog.ai as ai
    model = ai.provider("copilot").model("gpt-4o")
    agent = Agent(
        system_prompt="You are a helpful assistant. Keep your responses concise.",
        model=model,
    )

    stream = await agent.prompt("What is 2+2? Answer with just the number.")

    async for event in stream:
        pass

    assert not agent.state.is_streaming
    assert len(agent.state.messages) == 2
    assert agent.state.messages[0].role == "user"
    assert agent.state.messages[1].role == "assistant"

    content = agent.state.messages[1].content
    assert any("4" in c.text for c in content if hasattr(c, 'text'))

@pytest.mark.asyncio
async def test_e2e_tool_execution():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    import xdog.ai as ai
    model = ai.provider("copilot").model("gpt-4o")
    agent = Agent(
        system_prompt="You are a helpful assistant. Always use the calculate tool for math.",
        model=model,
        tools=[get_calculate_tool()],
    )

    stream = await agent.prompt("Calculate 123 * 456 using the tool.")

    async for event in stream:
        pass

    assert not agent.state.is_streaming
    assert len(agent.state.messages) >= 3
    assert any(m.role == "toolResult" for m in agent.state.messages)
