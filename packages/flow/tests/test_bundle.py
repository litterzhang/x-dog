"""Tests for flow.bundle — the portable self-contained bundle builder."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from xdog.flow.bundle import build_bundle
from xdog.flow.loader import parse_workflow
from xdog.flow.models import EdgeDef, NodeDef, Port, WorkflowDef

IN = "$in"
OUT = "$output"


def _script_wf() -> WorkflowDef:
    """A pure-script workflow: n -> n+1. No agent nodes, so a dry run needs no LLM."""
    return WorkflowDef(
        name="bundle-test",
        provider="copilot",
        entry="s",
        default_model="m",
        nodes=(
            NodeDef(
                id="s",
                type="script",
                code="def s(ctx, n):\n    return n + 1\n",
                input_ports=(Port("n", schema={"type": "integer"}),),
                output_ports=(Port("o", schema={"type": "integer"}),),
            ),
        ),
        edges=(EdgeDef(src=IN, dst="s", mapping=(("n", "n"),)),),
        initial_state=(("n", "41"),),
    )


def _agent_wf() -> WorkflowDef:
    """An SDK-agent workflow — its bundle vendors ai/agent."""
    return WorkflowDef(
        name="bundle-agent",
        provider="copilot",
        entry="a",
        default_model="m",
        nodes=(NodeDef(id="a", type="agent", prompt="hi", output_ports=(Port("out"),)),),
        edges=(EdgeDef(src="a", dst=OUT, mapping=(("out", "result"),)),),
    )


def test_bundle_layout(tmp_path: Path) -> None:
    out = build_bundle(_agent_wf(), tmp_path / "b")
    # Top-level files.
    for name in ("workflow.py", "__main__.py", "requirements.txt", "README.md"):
        assert (out / name).is_file(), f"missing {name}"
    # An SDK-agent bundle vendors ai/agent.
    assert (out / "_vendor" / "xdog" / "ai" / "__init__.py").is_file()
    assert (out / "_vendor" / "xdog" / "agent" / "__init__.py").is_file()
    assert sum(1 for _ in (out / "_vendor" / "xdog" / "ai").rglob("*.py")) > 10
    assert sum(1 for _ in (out / "_vendor" / "xdog" / "agent").rglob("*.py")) > 5


def test_bundle_script_only_drops_ai_agent(tmp_path: Path) -> None:
    """A pure-script (or pure-CLI) bundle vendors no ai/agent and trims requirements."""
    out = build_bundle(_script_wf(), tmp_path / "b")
    assert not (out / "_vendor" / "xdog" / "ai").exists()
    assert not (out / "_vendor" / "xdog" / "agent").exists()
    reqs = (out / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx" not in reqs and "pydantic" not in reqs
    assert "jsonpath-ng" in reqs


def test_bundle_excludes_caches_and_tests(tmp_path: Path) -> None:
    out = build_bundle(_script_wf(), tmp_path / "b")
    leftovers = [p for p in out.rglob("*") if p.is_dir() and p.name in ("__pycache__", "tests")]
    assert leftovers == [], f"vendored tree should skip caches/tests, found {leftovers}"


def test_bundle_workflow_compiles_without_flow_import(tmp_path: Path) -> None:
    out = build_bundle(_script_wf(), tmp_path / "b")
    src = (out / "workflow.py").read_text(encoding="utf-8")
    compile(src, "<workflow>", "exec")
    # The whole point: the bundled module never imports the flow package.
    assert "from flow." not in src
    assert "import flow" not in src


def test_bundle_requirements_are_pinned(tmp_path: Path) -> None:
    out = build_bundle(_agent_wf(), tmp_path / "b")
    reqs = (out / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx==" in reqs
    assert "pydantic==" in reqs


def test_bundle_main_bootstraps_vendor_path(tmp_path: Path) -> None:
    out = build_bundle(_script_wf(), tmp_path / "b")
    main = (out / "__main__.py").read_text(encoding="utf-8")
    assert "_vendor" in main
    assert "sys.path.insert" in main
    assert "from workflow import main" in main
    # -v / --verbose enables the node-event logging.
    assert "--verbose" in main and "basicConfig" in main


def test_bundle_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "b"
    target.mkdir()
    (target / "stale.txt").write_text("old")
    build_bundle(_script_wf(), target)
    assert not (target / "stale.txt").exists()  # dir was replaced
    assert (target / "workflow.py").is_file()


def test_bundle_runs_without_the_flow_package(tmp_path: Path) -> None:
    """A pure-script bundle runs in a subprocess whose sys.path excludes flow.

    Proves self-containment: only the bundle dir + its ``_vendor`` are on the
    path (never the flow package), yet the workflow executes and produces output.
    """
    out = build_bundle(_script_wf(), tmp_path / "b")
    # A driver that scrubs any x-dog path, keeps only stdlib + the bundle, and runs it.
    driver = textwrap.dedent(
        f"""
        import sys, asyncio
        from pathlib import Path
        b = Path({str(out)!r})
        # Drop the flow package SOURCE dir so the flow package is genuinely
        # unavailable — but keep site-packages (third-party deps like jsonpath_ng
        # / httpx that the generated module and vendored packages legitimately use).
        sys.path = [p for p in sys.path if "packages/flow/src" not in p]
        sys.path.insert(0, str(b / "_vendor"))
        sys.path.insert(0, str(b))
        import importlib.util
        assert importlib.util.find_spec("flow") is None, "flow must not be importable"
        import workflow
        asyncio.run(workflow.main())
        assert workflow._OUT["s"]["o"] == 42, workflow._OUT
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bundle failed to run:\n{result.stdout}\n{result.stderr}"
    assert "OK" in result.stdout


def test_bundle_carries_the_workflows_own_modules(tmp_path: Path) -> None:
    """A ``run:`` script node compiles to a real import, so its module must travel.

    The interpreter satisfies ``run: "helpers:step"`` by putting the workflow's own
    directory on sys.path; a bundle runs from somewhere else entirely, so without
    this the generated module raises ModuleNotFoundError — on a timer, unattended.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "helpers.py").write_text(
        "def step(ctx, n):\n    return int(n) + 1\n", encoding="utf-8"
    )
    # A peer that `helpers` reaches for at run time; flow cannot see this edge,
    # which is why the whole sibling set travels rather than just named modules.
    (src / "peer.py").write_text("VALUE = 1\n", encoding="utf-8")

    wf = parse_workflow({
        "name": "runref",
        "entry": "step",
        "in_schema": {"n": {"type": "integer"}},
        "state": {"n": 1},
        "nodes": [{
            "id": "step",
            "type": "script",
            "run": "helpers:step",
            "inputs": [{"name": "n", "schema": {"type": "integer"}, "required": True}],
            "outputs": [{"name": "out", "schema": {"type": "integer"}, "required": True}],
        }],
        "edges": [
            {"from": "$in", "to": "step", "map": {"n": "n"}},
            {"from": "step", "to": "$output", "map": {"out": "out"}},
        ],
    })

    out = build_bundle(wf, tmp_path / "bundle", base_dir=src)
    assert (out / "helpers.py").exists()
    assert (out / "peer.py").exists()
    # The generated module imports it by name, so the copy must not be shadowed.
    assert "from helpers import step" in (out / "workflow.py").read_text(encoding="utf-8")


def test_inline_script_workflow_copies_nothing(tmp_path: Path) -> None:
    """No ``run:`` reference means no external import, so no sibling sweep."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "unrelated.py").write_text("x = 1\n", encoding="utf-8")

    wf = parse_workflow({
        "name": "inline",
        "entry": "step",
        "in_schema": {"n": {"type": "integer"}},
        "state": {"n": 1},
        "nodes": [{
            "id": "step",
            "type": "script",
            "code": "def step(ctx, n):\n    return int(n) + 1\n",
            "inputs": [{"name": "n", "schema": {"type": "integer"}, "required": True}],
            "outputs": [{"name": "out", "schema": {"type": "integer"}, "required": True}],
        }],
        "edges": [
            {"from": "$in", "to": "step", "map": {"n": "n"}},
            {"from": "step", "to": "$output", "map": {"out": "out"}},
        ],
    })

    out = build_bundle(wf, tmp_path / "bundle", base_dir=src)
    assert not (out / "unrelated.py").exists()


def test_bundle_is_a_uv_project(tmp_path: Path) -> None:
    """The bundle ships a pyproject so `uv sync` alone can provision it.

    That is what the scheduler installer runs: one command that picks the
    interpreter, builds the venv, and installs the deps — no separate step for an
    operator to get wrong.
    """
    wf = parse_workflow({
        "name": "My Workflow!",
        "entry": "step",
        "in_schema": {"n": {"type": "integer"}},
        "state": {"n": 1},
        "nodes": [{
            "id": "step",
            "type": "script",
            "code": "def step(ctx, n):\n    return int(n) + 1\n",
            "inputs": [{"name": "n", "schema": {"type": "integer"}, "required": True}],
            "outputs": [{"name": "out", "schema": {"type": "integer"}, "required": True}],
        }],
        "edges": [
            {"from": "$in", "to": "step", "map": {"n": "n"}},
            {"from": "step", "to": "$output", "map": {"out": "out"}},
        ],
    })
    out = build_bundle(wf, tmp_path / "bundle")
    text = (out / "pyproject.toml").read_text(encoding="utf-8")

    # A workflow name is free-form; the project name must still be PEP 508-safe.
    assert 'name = "my-workflow"' in text
    assert "requires-python" in text
    # A bundle is a runnable directory, not a distribution — nothing to build.
    assert "package = false" in text
    # Every pinned requirement is declared, so pyproject and requirements.txt agree.
    for line in (out / "requirements.txt").read_text(encoding="utf-8").split():
        assert f'"{line}"' in text


def test_vendored_licences_come_from_distribution_metadata() -> None:
    """A pip-installed package keeps its licences in *.dist-info/licenses.

    The first version of this lookup only walked up from the package directory,
    which finds them in a source checkout and nowhere else — so it passed in
    development and shipped licence-less bundles to everyone who installed from
    PyPI. Assert against the metadata path directly.
    """
    from xdog.flow.bundle import _licence_files_for, _package_source_dir

    found = _licence_files_for("xdog.ai", _package_source_dir("xdog.ai"))
    assert found, "no licence located for the 'ai' package"
    names = {path.name for path in found}
    assert "LICENSE" in names, names
    # whatever the layout, the file must actually be the AGPL text
    assert "AFFERO" in found[0].read_text(encoding="utf-8", errors="replace").upper()


def test_unknown_package_yields_no_licences_rather_than_raising() -> None:
    """A missing licence is not fatal — a bundle without one is still runnable."""
    from xdog.flow.bundle import _licence_files_for

    assert _licence_files_for("no_such_package_xyz", Path("/nonexistent")) == []


def test_wheel_declares_examples_and_skill_as_package_data() -> None:
    """A wheel without them leaves a pip user with nothing to run or imitate.

    Asserted against the build config rather than the installed tree: in this
    workspace every package is installed editable, so `xdog/flow/examples` only
    exists once a wheel has actually been built. CI checks the built artifact
    itself; this catches the mapping being dropped from pyproject.
    """
    import tomllib

    cfg = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    forced = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert forced.get("examples") == "xdog/flow/examples"
    assert "skills" not in forced, (
        "the skill lives in the package source, not force-included -- see "
        "test_the_skill_is_discoverable_from_a_checkout for why"
    )
    # the sdist must NOT remap them: the wheel is built from the sdist, and
    # moving the sources would leave the wheel build with nothing to include
    sdist = cfg["tool"]["hatch"]["build"]["targets"].get("sdist", {})
    assert "force-include" not in sdist, "remapping in the sdist breaks the wheel build"


def test_the_skill_is_discoverable_from_a_checkout() -> None:
    """The property that matters, which the packaging line did not give.

    The skill used to be force-included into the wheel from outside the package,
    so `packaged_skills()` found it in production and found nothing in a
    checkout. Development is where a workflow's `skills:` reference gets written
    and tested, and it was the one environment where the skill did not exist --
    so an agent asked to write a flow workflow was silently never shown the
    format, and produced a plausible file with an invented node type that every
    downstream check accepted.
    """
    from xdog.agent.skills import load_packaged_skill, packaged_skills

    assert "flow-workflows" in packaged_skills()
    skill = load_packaged_skill("flow-workflows")
    assert skill is not None and skill.content.strip()
