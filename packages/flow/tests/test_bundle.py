"""Tests for flow.bundle — the portable self-contained bundle builder."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from flow.bundle import build_bundle
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef

IN = "$in"


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
                input_ports=(Port("n", "integer"),),
                output_ports=(Port("o", "integer"),),
            ),
        ),
        edges=(EdgeDef(src=IN, dst="s", mapping=(("n", "n"),)),),
        initial_state=(("n", "41"),),
    )


def test_bundle_layout(tmp_path: Path) -> None:
    out = build_bundle(_script_wf(), tmp_path / "b")
    # Top-level files.
    for name in ("workflow.py", "__main__.py", "requirements.txt", "README.md"):
        assert (out / name).is_file(), f"missing {name}"
    # Vendored packages present and non-trivial.
    assert (out / "_vendor" / "ai" / "__init__.py").is_file()
    assert (out / "_vendor" / "agent" / "__init__.py").is_file()
    assert sum(1 for _ in (out / "_vendor" / "ai").rglob("*.py")) > 10
    assert sum(1 for _ in (out / "_vendor" / "agent").rglob("*.py")) > 5


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
    out = build_bundle(_script_wf(), tmp_path / "b")
    reqs = (out / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx==" in reqs
    assert "pydantic==" in reqs


def test_bundle_main_bootstraps_vendor_path(tmp_path: Path) -> None:
    out = build_bundle(_script_wf(), tmp_path / "b")
    main = (out / "__main__.py").read_text(encoding="utf-8")
    assert "_vendor" in main
    assert "sys.path.insert" in main
    assert "from workflow import main" in main


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
        # Drop every x-dog source path so the flow package is genuinely unavailable.
        sys.path = [p for p in sys.path if "x-dog" not in p and "packages/flow" not in p]
        sys.path.insert(0, str(b / "_vendor"))
        sys.path.insert(0, str(b))
        import importlib.util
        assert importlib.util.find_spec("flow") is None, "flow must not be importable"
        import workflow
        asyncio.run(workflow.main())
        assert workflow._OUT["s"]["o"] == "42", workflow._OUT
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
