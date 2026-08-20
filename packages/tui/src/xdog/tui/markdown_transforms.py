"""Optional Markdown pre-render transforms."""

from __future__ import annotations

from collections.abc import Callable

MarkdownTransform = Callable[[str], str]

_TRANSFORMS: list[MarkdownTransform] = []


def register_markdown_transform(transform: MarkdownTransform) -> None:
    if transform not in _TRANSFORMS:
        _TRANSFORMS.append(transform)


def apply_markdown_transforms(text: str) -> str:
    for transform in tuple(_TRANSFORMS):
        text = transform(text)
    return text


def mermaid_fallback_transform(text: str) -> str:
    """Label Mermaid fences for terminals without a diagram renderer."""
    return text.replace("```mermaid", "```text\n[mermaid diagram]")


def unregister_markdown_transform(transform: MarkdownTransform) -> None:
    if transform in _TRANSFORMS:
        _TRANSFORMS.remove(transform)
