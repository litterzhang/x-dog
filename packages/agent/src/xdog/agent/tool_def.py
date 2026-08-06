"""ToolDef — declarative tool definitions with auto-generated schema and dispatch.

Usage with explicit Param::

    class MemoryTool(ToolDef):
        name = "memory"
        description = "Agent memory: get, search, write."

        @action("get", description="Read a file",
                filename=Param("string", required=True))
        async def get(self, ctx, filename: str):
            ...

Usage with type annotations (inferred)::

    class MemoryTool(ToolDef):
        name = "memory"
        description = "Agent memory: get, search, write."

        @action("get", description="Read a file")
        async def get(self, ctx, filename: str):
            # filename inferred as Param("string", required=True)
            ...

        @action("search", description="Keyword search")
        async def search(self, ctx, query: str, top_k: int = 5):
            # query: required string, top_k: optional integer with default 5
            ...

Explicit Param() overrides inferred params when both are present.

        @action("search", description="Keyword search",
                query=Param("string", required=True),
                top_k=Param("integer", default=5))
        async def search(self, ctx, query: str, top_k: int = 5):
            ...

    # Register:
    tool = MemoryTool().build()  # -> AgentTool
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, TypeVar, get_type_hints

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import TextContent

logger = logging.getLogger(__name__)


# Python type → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


_UNSET = object()


@dataclass
class Param:
    """Parameter declaration for a tool action.

    When ``required`` is not explicitly set and the handler signature
    has no default for this parameter, the param is treated as required.
    """

    type: str = "string"
    required: bool | object = field(default=_UNSET)
    description: str = ""
    default: Any = None
    enum: list[str] | None = None
    items: dict[str, Any] | None = None

    @property
    def is_required(self) -> bool:
        """Resolve required status. ``_UNSET`` is treated as False."""
        return self.required is True


_ACTION_ATTR = "_tool_action"

#: An action method, returned unchanged by @action so the decorated
#: signature survives type checking.
_ActionFn = TypeVar("_ActionFn", bound=Callable[..., Any])
_action_counter = 0


def action(
    name: str, description: str = "", **params: Param
) -> Callable[[_ActionFn], _ActionFn]:
    """Decorator that marks a method as a tool action.

    Actions are ordered by definition order, not alphabetically.

    The identity signature matters: without it every decorated method is
    "untyped" to a type checker, and so is everything that calls one. One
    unannotated decorator here silenced sixteen checks across five tool
    classes in another package.

    Usage::

        @action("get", description="Read a file",
                filename=Param("string", required=True))
        async def get(self, ctx, filename: str):
            ...
    """

    def decorator(fn: _ActionFn) -> _ActionFn:
        global _action_counter
        _action_counter += 1
        setattr(fn, _ACTION_ATTR, {
            "name": name,
            "description": description,
            "params": params,
            "order": _action_counter,
        })
        return fn

    return decorator


def _infer_params(method: Any) -> dict[str, Param]:
    """Infer Param declarations from a method's type annotations and defaults.

    Skips ``self`` and ``ctx`` parameters.  Maps Python types to JSON Schema
    types via ``_TYPE_MAP``.  A parameter with a default value becomes optional;
    one without becomes required.
    """
    sig = inspect.signature(method)
    try:
        hints = get_type_hints(method)
    except Exception:
        hints = {}

    params: dict[str, Param] = {}
    for name, p in sig.parameters.items():
        if name in ("self", "ctx"):
            continue
        hint = hints.get(name)
        if hint is None:
            continue

        json_type = _TYPE_MAP.get(hint)
        if json_type is None:
            # Unrecognised type — skip, caller must provide explicit Param
            continue

        has_default = p.default is not inspect.Parameter.empty
        params[name] = Param(
            type=json_type,
            required=not has_default,
            default=p.default if has_default else None,
        )
    return params


class ToolDef:
    """Base class for declarative tool definitions.

    Subclass this, set ``name`` and ``description``, and define
    ``@action``-decorated async methods. Call ``build()`` to produce
    an ``AgentTool``.

    For single-action tools, override ``execute(self, ctx, **params)``
    directly without ``@action``.  Declare ``params`` at the class
    level to generate a schema for single-action tools.

    Set ``required_ctx`` to declare which ctx keys the tool needs.
    The dispatch function validates their presence at call time and
    returns a clear error if any are missing.

    **Instance lifetime**: ``build()`` captures ``self`` in closures
    (bound methods, validator).  The ToolDef instance lives as long as
    the returned ``AgentTool``.  Do not rely on garbage collection
    after ``build()``.  Mutable state on ``self`` will persist and
    accumulate across tool calls.

    Example::

        class MemoryTool(ToolDef):
            name = "memory"
            required_ctx = ("workspace_dir",)

            @action("get", description="Read a file")
            async def get(self, ctx, filename: str):
                ...
    """

    name: str = ""
    description: str = ""
    required_ctx: tuple[str, ...] = ()
    params: dict[str, Param] | None = None
    """Class-level param declarations for single-action tools.

    For multi-action tools, use ``@action`` decorators instead.
    For single-action tools, set this to declare the JSON Schema::

        class TodoWriteTool(ToolDef):
            name = "todo_write"
            params = {
                "todos": Param("array", required=True, items={...}),
            }
    """

    def build(self) -> AgentTool:
        """Build an AgentTool from this definition.

        Introspects ``@action``-decorated methods to generate the JSON
        schema and dispatch function. If no ``@action`` methods exist,
        uses the ``execute`` method directly (single-action tool).

        For single-action tools, the schema is generated from the
        class-level ``params`` dict if provided.
        """
        actions = self._collect_actions()

        if actions:
            schema = self._build_multi_action_schema(actions)
            execute = self._build_multi_action_dispatch(actions)
            description = self._build_description_with_actions(actions)
        else:
            schema = self._build_single_action_schema()
            execute = self._build_single_action_dispatch()
            description = self.description

        return AgentTool(
            name=self.name,
            description=description,
            parameters=schema,
            execute=execute,
        )

    def _collect_actions(self) -> list[dict[str, Any]]:
        """Find all @action-decorated methods.

        Actions are returned in definition order (not alphabetical).
        Merges explicit Param() declarations with params inferred from
        type annotations.  Explicit Param wins for type/description/enum/
        items.  For ``required``: if the explicit Param didn't set it
        (left as ``_UNSET``), inherit from the inferred Param — so the
        handler signature determines required-ness.
        """
        raw: list[tuple[int, dict[str, Any]]] = []
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            method = getattr(self, attr_name, None)
            if method is None or not callable(method):
                continue
            info = getattr(method, _ACTION_ATTR, None)
            if info is None:
                continue

            explicit: dict[str, Param] = dict(info["params"])
            inferred = _infer_params(method)

            # Smart merge: explicit wins for type/desc/enum/items,
            # but required inherits from inferred when explicit didn't set it
            merged: dict[str, Param] = {}
            all_names = set(inferred) | set(explicit)
            for pname in all_names:
                inf = inferred.get(pname)
                exp = explicit.get(pname)
                if exp is None:
                    # Only inferred
                    merged[pname] = inf  # type: ignore[assignment]
                elif inf is None:
                    # Only explicit — resolve _UNSET required to False
                    if exp.required is _UNSET:
                        merged[pname] = Param(
                            type=exp.type, required=False, description=exp.description,
                            default=exp.default, enum=exp.enum, items=exp.items,
                        )
                    else:
                        merged[pname] = exp
                else:
                    # Both exist — explicit wins for type/desc/enum/items,
                    # required: explicit if set, else inherit from inferred
                    resolved_required = exp.required if exp.required is not _UNSET else inf.required
                    merged[pname] = Param(
                        type=exp.type,
                        required=resolved_required,
                        description=exp.description or inf.description,
                        default=exp.default if exp.default is not None else inf.default,
                        enum=exp.enum,
                        items=exp.items,
                    )

            raw.append((info["order"], {
                "name": info["name"],
                "description": info["description"],
                "params": merged,
                "method": method,
            }))
        raw.sort(key=lambda t: t[0])
        return [entry for _, entry in raw]

    def _build_description_with_actions(self, actions: list[dict]) -> str:
        """Append per-action summary to the tool description."""
        lines = [self.description, "", "Actions:"]
        for act in actions:
            required = [n for n, p in act["params"].items() if p.required is True]
            optional = [n for n, p in act["params"].items() if p.required is not True]
            parts = []
            if required:
                parts.append(", ".join(required))
            if optional:
                parts.append(f"optional: {', '.join(optional)}")
            param_str = f" ({'; '.join(parts)})" if parts else ""
            desc = f": {act['description']}" if act["description"] else ""
            lines.append(f"- {act['name']}{desc}{param_str}")
        return "\n".join(lines)

    def _build_multi_action_schema(self, actions: list[dict]) -> dict[str, Any]:
        """Generate JSON Schema with action enum + per-action param annotations.

        Each property's description is prefixed with ``[action1, action2]``
        to tell the LLM which action(s) use it.  If a param is used by
        every action, no prefix is added (it's universal).

        A param is added to the schema-level ``required`` array only if
        it is required by every action (universally required).  Per-action
        required enforcement is handled separately in the dispatch function.
        """
        action_names = [a["name"] for a in actions]
        properties: dict[str, Any] = {
            "action": {
                "type": "string",
                "enum": action_names,
                "description": "The action to perform.",
            },
        }
        required = ["action"]

        # Track which actions use each param, and whether all usages are required
        param_actions: dict[str, list[str]] = {}
        param_required_count: dict[str, int] = {}
        param_defs: dict[str, dict[str, Any]] = {}

        for act in actions:
            for param_name, param in act["params"].items():
                if param_name not in param_actions:
                    param_actions[param_name] = []
                    param_required_count[param_name] = 0
                param_actions[param_name].append(act["name"])
                if param.required is True:
                    param_required_count[param_name] += 1

                # First definition wins for the schema type
                if param_name not in param_defs:
                    prop: dict[str, Any] = {"type": param.type}
                    if param.description:
                        prop["description"] = param.description
                    if param.enum is not None:
                        prop["enum"] = param.enum
                    if param.default is not None:
                        prop["default"] = param.default
                    if param.items is not None:
                        prop["items"] = param.items
                    param_defs[param_name] = prop

        total_actions = len(actions)
        for param_name, prop in param_defs.items():
            used_by = param_actions[param_name]
            if len(used_by) < total_actions:
                prefix = f"[{', '.join(used_by)}]"
                existing = prop.get("description", "")
                prop["description"] = f"{prefix} {existing}" if existing else prefix
            properties[param_name] = prop

            # Universally required: used by all actions AND required in every one
            if len(used_by) == total_actions and param_required_count[param_name] == total_actions:
                required.append(param_name)

        return {"type": "object", "properties": properties, "required": required}

    def _build_single_action_schema(self) -> dict[str, Any]:
        """Build JSON Schema for a single-action tool from class-level params."""
        declared = self.params
        if not declared:
            # Also try inferring from execute() signature
            method = getattr(self, "execute", None)
            if method is not None:
                inferred = _infer_params(method)
                if inferred:
                    declared = inferred

        if not declared:
            return {"type": "object", "properties": {}}

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in declared.items():
            prop: dict[str, Any] = {"type": param.type}
            if param.description:
                prop["description"] = param.description
            if param.enum is not None:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            if param.items is not None:
                prop["items"] = param.items
            properties[param_name] = prop
            if param.required is True:
                required.append(param_name)

        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _validate_ctx(self, ctx: dict[str, Any] | None) -> str | None:
        """Return an error string if required ctx keys are missing, else None."""
        if not self.required_ctx:
            return None
        ctx = ctx or {}
        missing = [k for k in self.required_ctx if k not in ctx]
        if missing:
            return f"Tool {self.name!r} requires ctx keys: {', '.join(missing)}"
        return None

    def _build_multi_action_dispatch(self, actions: list[dict]):
        """Generate an async execute function that dispatches by action."""
        action_map = {a["name"]: a for a in actions}
        _validate = self._validate_ctx

        async def execute(
            tool_call_id: str,
            params: dict[str, Any],
            cancel: Any = None,
            on_update: Any = None,
            ctx: dict[str, Any] | None = None,
        ) -> AgentToolResult:
            ctx_error = _validate(ctx)
            if ctx_error:
                return AgentToolResult(content=(TextContent(text=f"Error: {ctx_error}"),))

            action_name = params.get("action", "")
            act = action_map.get(action_name)
            if act is None:
                names = ", ".join(action_map.keys())
                return AgentToolResult(
                    content=(TextContent(text=f"Error: unknown action {action_name!r}. Use: {names}"),),
                )

            # Validate required params before calling handler
            missing = [
                n for n, p in act["params"].items()
                if p.required is True and n not in params
            ]
            if missing:
                return AgentToolResult(
                    content=(TextContent(
                        text=f"Error: action {action_name!r} requires: {', '.join(missing)}",
                    ),),
                )

            # Build enriched context with cancel/on_update
            enriched_ctx = dict(ctx or {})
            enriched_ctx["_cancel"] = cancel
            enriched_ctx["_on_update"] = on_update

            # Extract params for this action
            kwargs = {}
            for param_name, param in act["params"].items():
                if param_name in params:
                    kwargs[param_name] = params[param_name]
                elif param.default is not None:
                    kwargs[param_name] = param.default

            try:
                result = await act["method"](enriched_ctx, **kwargs)
                return _wrap_result(result)
            except Exception as exc:
                logger.exception("Tool %s action %s failed", self.name, action_name)
                return AgentToolResult(
                    content=(TextContent(text=f"Error: {exc}"),),
                )

        return execute

    def _build_single_action_dispatch(self):
        """Generate an execute function for a single-action tool."""
        method = getattr(self, "execute", None)
        if method is None:
            raise ValueError(f"ToolDef {self.name!r} has no @action methods and no execute method")
        _validate = self._validate_ctx

        async def _execute(
            tool_call_id: str,
            params: dict[str, Any],
            cancel: Any = None,
            on_update: Any = None,
            ctx: dict[str, Any] | None = None,
        ) -> AgentToolResult:
            ctx_error = _validate(ctx)
            if ctx_error:
                return AgentToolResult(content=(TextContent(text=f"Error: {ctx_error}"),))

            enriched_ctx = dict(ctx or {})
            enriched_ctx["_cancel"] = cancel
            enriched_ctx["_on_update"] = on_update
            try:
                result = await method(enriched_ctx, **params)
                return _wrap_result(result)
            except Exception as exc:
                logger.exception("Tool %s execute failed", self.name)
                return AgentToolResult(
                    content=(TextContent(text=f"Error: {exc}"),),
                )

        return _execute


def _wrap_result(result: Any) -> AgentToolResult:
    """Wrap a handler return value into AgentToolResult."""
    if isinstance(result, AgentToolResult):
        return result
    if isinstance(result, str):
        return AgentToolResult(content=(TextContent(text=result),))
    return AgentToolResult(content=(TextContent(text=str(result)),))
