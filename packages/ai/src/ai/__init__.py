"""ai — Unified multi-provider LLM API.

Usage::

    import ai

    copilot = ai.provider("copilot")
    copilot.stream("claude-sonnet-4.5", context)

    runtime = ai.load()
    runtime.provider("copilot").stream(...)
"""

from ai.api import provider, load, login
from ai.core import BaseProvider, AuthResult
from ai.utils.event_stream import EventStream
from ai.types import (
    Model, ModelCost, Context, Usage,
    ThinkingLevel, ProviderType, StreamOptions,
    UserMessage, AssistantMessage, ToolResultMessage, Message,
    TextContent, ImageContent, ThinkingContent, ToolCall,
    StartEvent, DoneEvent, ErrorEvent, TextDeltaEvent, AssistantMessageEvent,
    EmbeddingObject, EmbeddingRequest, EmbeddingResponse,
    CostBreakdown, Tool,
    SystemPromptBlock, SystemPrompt, system_prompt_text,
)

__all__ = [
    # API
    "provider", "load", "login",
    # Interfaces
    "BaseProvider", "AuthResult", "EventStream",
    # Types
    "Model", "ModelCost", "Context", "Usage", "StreamOptions",
    "ThinkingLevel", "ProviderType",
    "UserMessage", "AssistantMessage", "ToolResultMessage", "Message",
    "TextContent", "ImageContent", "ThinkingContent", "ToolCall",
    "StartEvent", "DoneEvent", "ErrorEvent", "TextDeltaEvent", "AssistantMessageEvent",
    "EmbeddingObject", "EmbeddingRequest", "EmbeddingResponse",
    "CostBreakdown", "Tool",
]
