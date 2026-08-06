"""Keybinding registration and matching system.

Supports multi-modifier key descriptors such as ``"ctrl+shift+a"`` or ``"alt+enter"``.
Keybindings are organized into named scopes (contexts) that can be activated or
deactivated at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from xdog.tui.keys import KeyEvent


@dataclass(frozen=True, slots=True)
class Keybinding:
    """A single keybinding descriptor.

    Attributes:
        descriptor: Human-readable key combo, e.g. ``"ctrl+a"``.
        action: Name of the action to trigger.
        description: Optional human-readable help text.
    """

    descriptor: str
    action: str
    description: str = ""

    def matches(self, event: KeyEvent) -> bool:
        """Return ``True`` if *event* matches this binding's descriptor."""
        return event.matches(self.descriptor)


@dataclass
class KeybindingScope:
    """A named collection of keybindings that can be enabled/disabled together."""

    name: str
    enabled: bool = True
    bindings: list[Keybinding] = field(default_factory=list)

    def add(
        self,
        descriptor: str,
        action: str,
        description: str = "",
    ) -> Keybinding:
        """Register a keybinding in this scope and return it."""
        binding = Keybinding(
            descriptor=descriptor,
            action=action,
            description=description,
        )
        self.bindings.append(binding)
        return binding

    def match(self, event: KeyEvent) -> Keybinding | None:
        """Return the first matching binding for *event*, or ``None``."""
        if not self.enabled:
            return None
        for binding in self.bindings:
            if binding.matches(event):
                return binding
        return None


ActionHandler = Callable[[KeyEvent], None]


@dataclass
class KeybindingManager:
    """Central registry for keybinding scopes and action handlers.

    Scopes are checked in *reverse* registration order so that later (more
    specific) scopes take priority.
    """

    _scopes: list[KeybindingScope] = field(default_factory=list)
    _handlers: dict[str, ActionHandler] = field(default_factory=dict)

    # -- scope management -----------------------------------------------------

    def create_scope(self, name: str, enabled: bool = True) -> KeybindingScope:
        """Create and register a new scope."""
        scope = KeybindingScope(name=name, enabled=enabled)
        self._scopes.append(scope)
        return scope

    def get_scope(self, name: str) -> KeybindingScope | None:
        """Look up a scope by *name*."""
        for scope in self._scopes:
            if scope.name == name:
                return scope
        return None

    def enable_scope(self, name: str) -> None:
        scope = self.get_scope(name)
        if scope is not None:
            scope.enabled = True

    def disable_scope(self, name: str) -> None:
        scope = self.get_scope(name)
        if scope is not None:
            scope.enabled = False

    def remove_scope(self, name: str) -> None:
        self._scopes = [s for s in self._scopes if s.name != name]

    # -- handler management ---------------------------------------------------

    def register_handler(self, action: str, handler: ActionHandler) -> None:
        """Register a handler function for the given *action* name."""
        self._handlers[action] = handler

    def unregister_handler(self, action: str) -> None:
        self._handlers.pop(action, None)

    # -- matching & dispatching -----------------------------------------------

    def match(self, event: KeyEvent) -> Keybinding | None:
        """Find the first matching keybinding across all enabled scopes.

        Scopes are searched in reverse order (last registered first).
        """
        for scope in reversed(self._scopes):
            binding = scope.match(event)
            if binding is not None:
                return binding
        return None

    def dispatch(self, event: KeyEvent) -> bool:
        """Match *event* against all scopes and invoke the handler if found.

        Returns ``True`` if a handler was invoked, ``False`` otherwise.
        """
        binding = self.match(event)
        if binding is None:
            return False
        handler = self._handlers.get(binding.action)
        if handler is not None:
            handler(event)
            return True
        return False

    # -- introspection --------------------------------------------------------

    def all_bindings(self) -> list[tuple[str, Keybinding]]:
        """Return ``(scope_name, binding)`` pairs for every registered binding."""
        result: list[tuple[str, Keybinding]] = []
        for scope in self._scopes:
            for binding in scope.bindings:
                result.append((scope.name, binding))
        return result
