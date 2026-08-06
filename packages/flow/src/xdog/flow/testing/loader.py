"""flow.testing.loader — parse and validate a ``*.test.json`` suite.

Every authoring mistake is reported here, before anything executes: a stub aimed at
a node that does not exist, at the wrong node *type*, or setting an output port the
node never declared.  That check is what keeps a suite honest — without it a typo
silently falls through to a default rule and the case still "passes".

Discovery is by convention: ``release_readiness.json`` pairs with
``release_readiness.test.json``.  A suite may name its workflow explicitly, which
is what lets a suite live somewhere other than beside its workflow.
"""

from __future__ import annotations

import json
from pathlib import Path

from xdog.flow.builder.io import load_any
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.models import NodeDef, WorkflowDef
from xdog.flow.testing.models import Case, Expect, NodeStub, StubRule, Suite

TEST_SUFFIX = ".test.json"

_STUB_KINDS = {"agents": "agent", "subflows": "subflow", "scripts": "script"}
_RULE_KEYS = {"when", "index", "round", "then", "tokens"}
_CASE_KEYS = {"name", "inputs", "signals", "max_tokens", "agents", "subflows", "scripts", "expect"}
_EXPECT_KEYS = {"success", "error", "paused", "output", "calls"}


def suite_path_for(path: Path) -> Path:
    """Map any accepted path to its suite file.

    Accepts the suite itself, or the workflow it targets (``foo.json`` ->
    ``foo.test.json``), so ``xdog-flow test foo.json`` reads naturally.
    """
    if path.name.endswith(TEST_SUFFIX):
        return path
    return path.with_suffix("").with_name(path.stem + TEST_SUFFIX)


def discover(path: Path) -> list[Path]:
    """All suite files reachable from *path* (a file or a directory), sorted."""
    if path.is_dir():
        return sorted(p for p in path.rglob("*" + TEST_SUFFIX) if p.is_file())
    resolved = suite_path_for(path)
    if not resolved.exists():
        raise WorkflowValidationError(f"no test suite at {resolved}")
    return [resolved]


def load_suite(path: Path, *, allow_script_stub: bool = False) -> tuple[Suite, WorkflowDef]:
    """Load and fully validate the suite at *path* against its workflow."""
    suite_file = suite_path_for(path)
    try:
        raw = json.loads(suite_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowValidationError(f"{suite_file}: invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowValidationError(f"{suite_file}: top level must be an object")

    declared = raw.get("workflow")
    if declared is not None and not isinstance(declared, str):
        raise WorkflowValidationError(f"{suite_file}: 'workflow' must be a string path")
    workflow_file = (
        (suite_file.parent / declared).resolve()
        if declared
        else suite_file.with_name(suite_file.name[: -len(TEST_SUFFIX)] + ".json")
    )
    if not workflow_file.exists():
        raise WorkflowValidationError(f"{suite_file}: workflow not found at {workflow_file}")
    wf = load_any(str(workflow_file))

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise WorkflowValidationError(f"{suite_file}: 'cases' must be a non-empty array")

    nodes = {n.id: n for n in wf.nodes}
    seen: set[str] = set()
    cases: list[Case] = []
    for i, raw_case in enumerate(raw_cases):
        case = _parse_case(raw_case, nodes, where=f"{suite_file}: cases[{i}]", allow_script_stub=allow_script_stub)
        if case.name in seen:
            raise WorkflowValidationError(f"{suite_file}: duplicate case name {case.name!r}")
        seen.add(case.name)
        cases.append(case)

    return Suite(path=suite_file, workflow_path=workflow_file, cases=tuple(cases)), wf


def _parse_case(
    raw: object,
    nodes: dict[str, NodeDef],
    *,
    where: str,
    allow_script_stub: bool,
) -> Case:
    if not isinstance(raw, dict):
        raise WorkflowValidationError(f"{where}: must be an object")
    _reject_unknown(raw, _CASE_KEYS, where=where)

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowValidationError(f"{where}: 'name' is required and must be a non-empty string")
    where = f"{where} ({name})"

    inputs = _object(raw.get("inputs", {}), where=f"{where}: 'inputs'")

    raw_signals = raw.get("signals", [])
    if not isinstance(raw_signals, list) or not all(isinstance(s, str) for s in raw_signals):
        raise WorkflowValidationError(f"{where}: 'signals' must be an array of strings")

    max_tokens = raw.get("max_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or isinstance(max_tokens, bool)):
        raise WorkflowValidationError(f"{where}: 'max_tokens' must be an integer")

    stubs: dict[str, dict[str, NodeStub]] = {}
    for key, node_type in _STUB_KINDS.items():
        raw_stubs = _object(raw.get(key, {}), where=f"{where}: {key!r}")
        if key == "scripts" and raw_stubs and not allow_script_stub:
            raise WorkflowValidationError(
                f"{where}: stubbing script nodes hides deterministic logic from the test; "
                f"pass --allow-script-stub to opt in"
            )
        stubs[key] = {
            node_id: _parse_stub(node_id, value, nodes, node_type=node_type, where=f"{where}: {key}.{node_id}")
            for node_id, value in raw_stubs.items()
        }

    return Case(
        name=name,
        inputs=inputs,
        signals=frozenset(raw_signals),
        max_tokens=max_tokens,
        agents=stubs["agents"],
        subflows=stubs["subflows"],
        scripts=stubs["scripts"],
        expect=_parse_expect(raw.get("expect", {}), nodes, where=f"{where}: 'expect'"),
    )


def _parse_stub(
    node_id: str,
    raw: object,
    nodes: dict[str, NodeDef],
    *,
    node_type: str,
    where: str,
) -> NodeStub:
    node = nodes.get(node_id)
    if node is None:
        known = ", ".join(sorted(n for n, d in nodes.items() if d.type == node_type)) or "(none)"
        raise WorkflowValidationError(f"{where}: no such node; {node_type} nodes are: {known}")
    if node.type != node_type:
        raise WorkflowValidationError(
            f"{where}: node {node_id!r} is a {node.type} node, not {node_type}; "
            f"move the stub under {_key_for(node.type)!r}"
        )

    if isinstance(raw, dict):  # constant stub -> a single default rule
        return NodeStub(node_id=node_id, rules=(StubRule(then=_stub_ports(raw, node, where=where)),))
    if not isinstance(raw, list) or not raw:
        raise WorkflowValidationError(f"{where}: must be an output-port object or a non-empty array of rules")

    rules: list[StubRule] = []
    for i, raw_rule in enumerate(raw):
        rules.append(_parse_rule(raw_rule, node, where=f"{where}[{i}]"))
    for rule in rules[:-1]:
        if rule.is_default:
            raise WorkflowValidationError(
                f"{where}: a rule with no selector matches everything, so it must be last"
            )
    return NodeStub(node_id=node_id, rules=tuple(rules))


def _parse_rule(raw: object, node: NodeDef, *, where: str) -> StubRule:
    if not isinstance(raw, dict):
        raise WorkflowValidationError(f"{where}: must be an object")
    _reject_unknown(raw, _RULE_KEYS, where=where)
    if "then" not in raw:
        raise WorkflowValidationError(f"{where}: missing 'then' (the node's output ports)")

    when = raw.get("when")
    if when is not None and not isinstance(when, dict):
        raise WorkflowValidationError(f"{where}: 'when' must be an object matched against the node's inputs")
    index = _positive_int(raw.get("index"), where=f"{where}: 'index'", minimum=0)
    round_ = _positive_int(raw.get("round"), where=f"{where}: 'round'", minimum=1)
    tokens = _positive_int(raw.get("tokens"), where=f"{where}: 'tokens'", minimum=0) or 0

    return StubRule(
        then=_stub_ports(raw["then"], node, where=f"{where}: 'then'"),
        when=when,
        index=index,
        round=round_,
        tokens=tokens,
    )


def _stub_ports(raw: object, node: NodeDef, *, where: str) -> dict[str, object]:
    """Validate a stub's payload against the node's declared output ports.

    Unknown keys are always an error (they are silently ignored at runtime, so a
    typo would otherwise produce a passing test asserting nothing).  Missing keys
    are left to the production path, which already rejects them for a structured
    agent and legitimately allows them for a subflow projection.
    """
    ports = _object(raw, where=where)
    declared = {p.name for p in node.output_ports}
    unknown = sorted(set(ports) - declared)
    if unknown:
        listed = ", ".join(sorted(declared)) or "(none)"
        raise WorkflowValidationError(
            f"{where}: node {node.id!r} has no output port(s) {', '.join(unknown)}; declared: {listed}"
        )
    if node.type == "agent" and len(declared) == 1 and not ports:
        raise WorkflowValidationError(f"{where}: must set the node's output port {next(iter(declared))!r}")
    return ports


def _parse_expect(raw: object, nodes: dict[str, NodeDef], *, where: str) -> Expect:
    body = _object(raw, where=where)
    _reject_unknown(body, _EXPECT_KEYS, where=where)

    declared = [k for k in ("success", "error", "paused") if k in body]
    if len(declared) > 1:
        raise WorkflowValidationError(f"{where}: {' and '.join(declared)} are mutually exclusive")

    outcome: str = "success"
    error: str | None = None
    paused: str | None = None
    if "error" in body:
        if not isinstance(body["error"], str) or not body["error"]:
            raise WorkflowValidationError(f"{where}: 'error' must be a non-empty substring to match")
        outcome, error = "error", body["error"]
    elif "paused" in body:
        node_id = body["paused"]
        if not isinstance(node_id, str):
            raise WorkflowValidationError(f"{where}: 'paused' must be the id of the human node")
        human = nodes.get(node_id)
        if human is None or human.type != "human":
            known = ", ".join(sorted(n for n, d in nodes.items() if d.type == "human")) or "(none)"
            raise WorkflowValidationError(f"{where}: 'paused' must name a human node; human nodes are: {known}")
        outcome, paused = "paused", node_id
    elif "success" in body:
        if not isinstance(body["success"], bool):
            raise WorkflowValidationError(f"{where}: 'success' must be a boolean")
        if not body["success"]:
            raise WorkflowValidationError(
                f"{where}: use 'error' with the expected message (or 'paused') instead of success=false"
            )

    calls = _object(body.get("calls", {}), where=f"{where}: 'calls'")
    for node_id, count in calls.items():
        if node_id not in nodes:
            raise WorkflowValidationError(f"{where}: calls.{node_id}: no such node")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise WorkflowValidationError(f"{where}: calls.{node_id}: must be a non-negative integer")

    return Expect(
        outcome=outcome,  # type: ignore[arg-type]
        error=error,
        paused=paused,
        output=_object(body.get("output", {}), where=f"{where}: 'output'"),
        calls={k: int(v) for k, v in calls.items() if isinstance(v, int)},
    )


def _key_for(node_type: str) -> str:
    for key, kind in _STUB_KINDS.items():
        if kind == node_type:
            return key
    return node_type


def _object(raw: object, *, where: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise WorkflowValidationError(f"{where}: must be an object")
    return dict(raw)


def _positive_int(raw: object, *, where: str, minimum: int) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum:
        raise WorkflowValidationError(f"{where}: must be an integer >= {minimum}")
    return raw


def _reject_unknown(raw: dict[str, object], allowed: set[str], *, where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise WorkflowValidationError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )
