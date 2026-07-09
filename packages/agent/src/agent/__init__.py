"""agent - Agent runtime with tool calling and state management.

Quick start::

    import ai
    from agent import Agent, AgentConfig
    from agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

    agent = Agent(
        stream_fn_from_provider(ai.provider("copilot")),
        config=AgentConfig(model="claude-sonnet-4.5", system_prompt="You are helpful."),
        tools=[my_tool],
    )

    stream = await agent.prompt("Hello!")
    async for event in stream:
        print(event.type)
"""

# -- Core -------------------------------------------------------------------
from agent.core import (
    AgentConfig,
    AgentContext,
    AgentMessage,
    AgentState,
    AgentTool,
    AgentToolResult,
    AgentToolUpdateCallback,
    CustomAgentMessage,
    EmbedFn,
    QueueMode,
    StreamFn,
    ToolExecutionMode,
    WebSearchFn,
)

# -- Events -----------------------------------------------------------------
from agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

# -- Types (hooks, callbacks, loop config) ----------------------------------
from agent.types import (
    AfterToolCallContext,
    AfterToolCallFn,
    AfterToolCallResult,
    AgentEventSink,
    AgentLoopConfig,
    BeforeToolCallContext,
    BeforeToolCallFn,
    BeforeToolCallResult,
    ConvertToLlmFn,
    GetMessagesFn,
    TransformContextFn,
)

# -- Agent class ------------------------------------------------------------
from agent.agent import Agent, EventListener

# -- Agent loop functions ---------------------------------------------------
from agent.agent_loop import (
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,
)

# -- Event stream -----------------------------------------------------------
from agent.event_stream import AgentEventStream

# -- Helpers ------------------------------------------------
from agent.helpers import (
    stream_fn_from_provider,
    web_search_fn_from_provider,
    embed_fn_from_provider,
)

# -- ToolDef framework -----------------------------------------------------
from agent.tool_def import ToolDef, Param, action

# -- Built-in tools ---------------------------------------------------------
from agent.tools import (
    create_bash_tool,
    create_current_time_tool,
    create_embed_tool_from_fn,
    create_filesystem_tool,
    create_web_search_tool_from_fn,
)

# -- Tool SPI ---------------------------------------------------------------
from agent.tools.registry import (
    register_tool,
    unregister_tool,
    get_registered_tools,
    registered_tool_names,
)

__all__ = [
    # Core
    "AgentConfig",
    "AgentContext",
    "AgentMessage",
    "AgentState",
    "AgentTool",
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "CustomAgentMessage",
    "EmbedFn",
    "QueueMode",
    "StreamFn",
    "ToolExecutionMode",
    "WebSearchFn",
    # Events
    "AgentEndEvent",
    "AgentEvent",
    "AgentStartEvent",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    # Types (hooks, callbacks, loop config)
    "AfterToolCallContext",
    "AfterToolCallFn",
    "AfterToolCallResult",
    "AgentEventSink",
    "AgentLoopConfig",
    "BeforeToolCallContext",
    "BeforeToolCallFn",
    "BeforeToolCallResult",
    "ConvertToLlmFn",
    "GetMessagesFn",
    "TransformContextFn",
    # Agent class
    "Agent",
    "EventListener",
    # Loop functions
    "agent_loop",
    "agent_loop_continue",
    "run_agent_loop",
    "run_agent_loop_continue",
    # Event stream
    "AgentEventStream",
    # Helpers
    "stream_fn_from_provider",
    "web_search_fn_from_provider",
    "embed_fn_from_provider",
    # ToolDef framework
    "ToolDef",
    "Param",
    "action",
    # Built-in tools
    "create_bash_tool",
    "create_current_time_tool",
    "create_embed_tool_from_fn",
    "create_filesystem_tool",
    "create_web_search_tool_from_fn",
    # Tool SPI
    "register_tool",
    "unregister_tool",
    "get_registered_tools",
    "registered_tool_names",
]
