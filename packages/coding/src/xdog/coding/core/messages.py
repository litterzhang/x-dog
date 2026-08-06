"""Message serialization — moved to :mod:`xdog.agent.messages`.

It was never coding-specific: claw had written its own copy, and flow needed a
third. Re-exported here so existing callers are untouched.
"""

from __future__ import annotations

from xdog.agent.messages import (  # noqa: F401
    dict_to_message,
    dicts_to_messages,
    message_to_dict,
    messages_to_dicts,
)

__all__ = ["dict_to_message", "dicts_to_messages", "message_to_dict", "messages_to_dicts"]
