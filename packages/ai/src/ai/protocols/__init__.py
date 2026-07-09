"""Wire-format protocol implementations.

Each module exposes a :class:`~ai.core.BaseProtocol` subclass:

- ``openai_completions.py`` → :class:`OpenAICompletionsProtocol` (stream + embed)
- ``anthropic_messages.py`` → :class:`AnthropicMessagesProtocol` (stream)
- ``openai_responses.py``   → :class:`OpenAIResponsesProtocol` (stream)
"""

__all__: list[str] = []
