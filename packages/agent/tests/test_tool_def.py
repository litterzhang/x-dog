"""Tests for ToolDef — declarative tool definitions with schema generation."""


import pytest
from xdog.agent.tool_def import Param, ToolDef, action

# ---------------------------------------------------------------------------
# Type annotation inference
# ---------------------------------------------------------------------------

class _InferTool(ToolDef):
    name = "infer"
    description = "Test inference"

    @action("search", description="Search things")
    async def search(self, ctx, query: str, top_k: int = 5):
        return f"query={query} top_k={top_k}"

    @action("toggle", description="Toggle flag")
    async def toggle(self, ctx, enabled: bool):
        return f"enabled={enabled}"

    @action("rate", description="Rate something")
    async def rate(self, ctx, score: float, tags: list = []):
        return f"score={score}"

def test_infer_skips_self_and_ctx():
    """self and ctx should never appear in params."""
    tool = _InferTool()
    actions = tool._collect_actions()
    for act in actions:
        assert "self" not in act["params"]
        assert "ctx" not in act["params"]

# ---------------------------------------------------------------------------
# Explicit Param overrides inferred
# ---------------------------------------------------------------------------

class _OverrideTool(ToolDef):
    name = "override"
    description = "Test override"

    @action("find", description="Find stuff",
            query=Param("string", required=True, description="Search query"))
    async def find(self, ctx, query: str, limit: int = 10):
        return f"query={query}"

def test_explicit_overrides_inferred():
    """Explicit Param() should override the inferred one."""
    tool = _OverrideTool()
    actions = tool._collect_actions()
    find = actions[0]

    # query: explicit Param wins -- has description
    q = find["params"]["query"]
    assert q.description == "Search query"
    assert q.required is True

    # limit: inferred only (no explicit Param)
    lim = find["params"]["limit"]
    assert lim.type == "integer"
    assert lim.required is False
    assert lim.default == 10

# ---------------------------------------------------------------------------
# Schema generation with inferred params
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dispatch with inferred params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_with_inferred_params():
    """Dispatched call should receive inferred default values."""
    tool = _InferTool().build()

    # Call search with only query -- top_k should default to 5
    result = await tool.execute("tc1", {"action": "search", "query": "hello"})
    assert result.content[0].text == "query=hello top_k=5"

    # Call search with explicit top_k
    result = await tool.execute("tc2", {"action": "search", "query": "hi", "top_k": 3})
    assert result.content[0].text == "query=hi top_k=3"

# ---------------------------------------------------------------------------
# No annotations -- pure explicit Param
# ---------------------------------------------------------------------------

class _ExplicitOnlyTool(ToolDef):
    name = "explicit"
    description = "All params explicit"

    @action("run", description="Run it",
            cmd=Param("string", required=True))
    async def run(self, ctx, cmd):
        return f"cmd={cmd}"

# ---------------------------------------------------------------------------
# Single-action tool (no @action)
# ---------------------------------------------------------------------------

class _SingleTool(ToolDef):
    name = "single"
    description = "Single action tool"

    async def execute(self, ctx, **params):
        return f"got {params}"

@pytest.mark.asyncio
async def test_single_action_tool():
    """Single-action tool should still work without @action."""
    tool = _SingleTool().build()
    result = await tool.execute("tc1", {"key": "value"})
    assert "key" in result.content[0].text

# ---------------------------------------------------------------------------
# _infer_params directly
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Definition-order (not alphabetical) action ordering
# ---------------------------------------------------------------------------

class _OrderedTool(ToolDef):
    name = "ordered"
    description = "Test ordering"

    @action("zebra", description="Z action")
    async def zebra(self, ctx):
        return "z"

    @action("alpha", description="A action")
    async def alpha(self, ctx):
        return "a"

    @action("middle", description="M action")
    async def middle(self, ctx):
        return "m"

# ---------------------------------------------------------------------------
# Per-action param annotation in schema descriptions
# ---------------------------------------------------------------------------

class _MultiParamTool(ToolDef):
    name = "multi"
    description = "Test param annotations"

    @action("write", description="Write data")
    async def write(self, ctx, path: str, content: str):
        return "ok"

    @action("read", description="Read data")
    async def read(self, ctx, path: str, limit: int = 100):
        return "ok"

def test_schema_annotates_action_specific_params():
    """Params used by a subset of actions get [action] prefix in description."""
    tool = _MultiParamTool().build()
    props = tool.parameters["properties"]

    # 'path' is used by both actions -> no prefix
    assert not props["path"].get("description", "").startswith("[")

    # 'content' is only used by 'write'
    assert props["content"]["description"].startswith("[write]")

    # 'limit' is only used by 'read'
    assert props["limit"]["description"].startswith("[read]")

# ---------------------------------------------------------------------------
# required_ctx validation
# ---------------------------------------------------------------------------

class _CtxRequiredTool(ToolDef):
    name = "ctx_req"
    description = "Tool requiring ctx keys"
    required_ctx = ("workspace_dir", "group_id")

    @action("run", description="Do something")
    async def run(self, ctx, value: str):
        return f"ws={ctx['workspace_dir']}"

@pytest.mark.asyncio
async def test_required_ctx_missing():
    """Tool should return error when required ctx keys are missing."""
    tool = _CtxRequiredTool().build()
    result = await tool.execute("tc1", {"action": "run", "value": "x"}, ctx={})
    text = result.content[0].text
    assert "Error" in text
    assert "workspace_dir" in text
    assert "group_id" in text

@pytest.mark.asyncio
async def test_required_ctx_present():
    """Tool should work when required ctx keys are present."""
    tool = _CtxRequiredTool().build()
    result = await tool.execute("tc1", {"action": "run", "value": "x"},
                                ctx={"workspace_dir": "/tmp", "group_id": "main"})
    assert "ws=/tmp" in result.content[0].text

class _NoCtxRequiredTool(ToolDef):
    name = "no_ctx"
    description = "No ctx requirement"

    @action("run", description="Do it")
    async def run(self, ctx, value: str):
        return "ok"

# ---------------------------------------------------------------------------
# required_ctx on single-action tool
# ---------------------------------------------------------------------------

class _SingleCtxTool(ToolDef):
    name = "single_ctx"
    description = "Single action with ctx requirement"
    required_ctx = ("data_dir",)

    async def execute(self, ctx, **params):
        return f"dir={ctx['data_dir']}"

# ---------------------------------------------------------------------------
# Required param enforcement
# ---------------------------------------------------------------------------

class _RequiredParamTool(ToolDef):
    name = "strict"
    description = "Test required enforcement"

    @action("create", description="Create something")
    async def create(self, ctx, title: str, description: str, tags: list = []):
        return f"created {title}"

    @action("list", description="List things")
    async def list(self, ctx, limit: int = 10):
        return "listed"

@pytest.mark.asyncio
async def test_required_param_missing_returns_error():
    """Omitting a required param should return a clear error, not a TypeError."""
    tool = _RequiredParamTool().build()
    # Call create without required 'title' and 'description'
    result = await tool.execute("tc1", {"action": "create"})
    text = result.content[0].text
    assert "Error" in text
    assert "title" in text
    assert "description" in text

# ---------------------------------------------------------------------------
# Single-action tool schema from class-level params
# ---------------------------------------------------------------------------

class _SchemaFromParams(ToolDef):
    name = "with_schema"
    description = "Single-action with schema"
    params = {
        "query": Param("string", required=True, description="Search text"),
        "limit": Param("integer", default=10),
    }

    async def execute(self, ctx, **params):
        return f"query={params.get('query')}"

class _SchemaInferred(ToolDef):
    name = "inferred_single"
    description = "Single-action with inferred schema"

    async def execute(self, ctx, name: str, count: int = 5):
        return f"{name}={count}"

# ---------------------------------------------------------------------------
# Schema-level required for universally required params
# ---------------------------------------------------------------------------

class _UniversalRequiredTool(ToolDef):
    name = "universal"
    description = "Test universal required"

    @action("read", description="Read")
    async def read(self, ctx, path: str):
        return "ok"

    @action("write", description="Write")
    async def write(self, ctx, path: str, content: str):
        return "ok"

class _NoUniversalTool(ToolDef):
    name = "no_universal"
    description = "No universally required params"

    @action("create", description="Create")
    async def create(self, ctx, title: str):
        return "ok"

    @action("list", description="List")
    async def list(self, ctx, limit: int = 10):
        return "ok"

class _MixedRequiredTool(ToolDef):
    name = "mixed"
    description = "Param used by all but optional in one"

    @action("a", description="A")
    async def a(self, ctx, key: str):
        return "ok"

    @action("b", description="B")
    async def b(self, ctx, key: str = "default"):
        return "ok"

def test_schema_required_not_universal_when_optional_somewhere():
    """A param used by all actions but optional in one is NOT schema-required."""
    tool = _MixedRequiredTool().build()
    required = tool.parameters.get("required", [])
    # 'key' is used by both but optional in 'b' -> not universally required
    assert "key" not in required

# ---------------------------------------------------------------------------
# Param.required inherits from handler signature
# ---------------------------------------------------------------------------

class _ExplicitParamNoRequired(ToolDef):
    name = "inherit_req"
    description = "Test required inheritance"

    @action("get", description="Get by label",
            label=Param("string", description="The label to look up"))
    async def get(self, ctx, label: str):
        return f"got {label}"

    @action("search", description="Search",
            query=Param("string", description="Search text"),
            limit=Param("integer", description="Max results"))
    async def search(self, ctx, query: str, limit: int = 10):
        return f"found {query}"

class _ExplicitRequiredFalse(ToolDef):
    name = "explicit_false"
    description = "Explicitly optional despite no default"

    @action("get", description="Get",
            label=Param("string", required=False, description="Optional label"))
    async def get(self, ctx, label: str = ""):
        return f"got {label}"
