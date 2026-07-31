"""flow.bundle — package a generated workflow into a self-contained directory.

``xdog-flow generate --portable`` emits a *bundle*: the generated ``workflow.py``
plus vendored copies of the ``ai`` and ``agent`` packages (the only two the
generated module imports at run time, now that flow-internal helpers are inlined),
a ``__main__.py`` that puts the vendored packages on ``sys.path``, a pinned
``requirements.txt`` for the remaining third-party deps (httpx, pydantic), and a
README.

The result runs without ``xdog-ai`` / ``xdog-agent`` installed::

    pip install -r <bundle>/requirements.txt
    python <bundle>            # or: python <bundle> --dry-run  (no auth needed)

With ``offline=True`` the third-party wheels are downloaded into
``_vendor/wheels/`` so the bundle installs with ``pip install --no-index``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

from flow.codegen import generate
from flow.models import WorkflowDef

# Packages the generated module imports at run time. flow itself is NOT needed —
# errors/coerce/runtime and the tool registry are inlined into workflow.py.
_VENDORED_PACKAGES = ("ai", "agent")

# Third-party runtime dependencies of the vendored packages (ai needs httpx +
# pydantic; agent needs fastjsonschema for submit_result schema validation; the
# generated module imports jsonpath-ng for interpolation/conditions).
_THIRD_PARTY = ("httpx", "pydantic", "fastjsonschema", "jsonpath-ng")

# Directory names to skip when copying a package's source tree.
_SKIP_DIRS = {"__pycache__", "tests", ".mypy_cache", ".ruff_cache"}


def _package_source_dir(name: str) -> Path:
    """Return the on-disk source directory of an importable top-level package."""
    module = __import__(name)
    file = getattr(module, "__file__", None)
    if file is None:  # namespace package — no single dir
        raise RuntimeError(f"cannot locate source directory for package {name!r}")
    return Path(file).resolve().parent


def _copy_package(name: str, vendor_dir: Path) -> None:
    """Copy ``<name>``'s source tree into *vendor_dir*, skipping caches and tests."""
    src = _package_source_dir(name)
    dst = vendor_dir / name
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(*_SKIP_DIRS, "*.pyc"),
    )


def _pin(dist: str) -> str:
    """Return ``"dist==<installed version>"``, or a bare ``dist`` if not found."""
    try:
        return f"{dist}=={importlib.metadata.version(dist)}"
    except importlib.metadata.PackageNotFoundError:
        return dist


def _render_main() -> str:
    """The bundle's ``__main__.py`` — prepend ``_vendor`` to sys.path, run main()."""
    return (
        '"""Entry point: run the bundled workflow with vendored ai/agent on sys.path."""\n'
        "\n"
        "import asyncio\n"
        "import logging\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "_HERE = Path(__file__).resolve().parent\n"
        "# Vendored ai/agent take precedence over any system install.\n"
        "sys.path.insert(0, str(_HERE / '_vendor'))\n"
        "sys.path.insert(0, str(_HERE))\n"
        "\n"
        "from workflow import main  # noqa: E402  (after sys.path setup)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    if '-v' in sys.argv or '--verbose' in sys.argv:\n"
        "        # Surface the P3 node lifecycle events the workflow logs to\n"
        "        # 'flow.generated.events'; off by default (JSON output only).\n"
        "        logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "    asyncio.run(main())\n"
    )


def _render_readme(wf: WorkflowDef, requirements: list[str], offline: bool) -> str:
    """Human-facing run instructions for the bundle."""
    reqs = "\n".join(f"  - {r}" for r in requirements)
    if offline:
        install = (
            "pip install --no-index --find-links _vendor/wheels -r requirements.txt"
        )
    else:
        install = "pip install -r requirements.txt"
    return (
        f"# {wf.name} — portable workflow bundle\n"
        "\n"
        "A self-contained copy of this workflow. It vendors the `ai` and `agent`\n"
        "packages under `_vendor/`, so it runs without `xdog-ai` / `xdog-agent`\n"
        "installed.\n"
        "\n"
        "## Layout\n"
        "\n"
        "- `workflow.py` — the generated workflow module.\n"
        "- `__main__.py` — entry point (puts `_vendor/` on `sys.path`, runs `main()`).\n"
        "- `_vendor/ai`, `_vendor/agent` — vendored package sources.\n"
        "- `requirements.txt` — pinned third-party runtime dependencies.\n"
        + ("- `_vendor/wheels/` — downloaded wheels for offline install.\n" if offline else "")
        + "\n"
        "## Run\n"
        "\n"
        "```sh\n"
        f"{install}\n"
        "python .              # real run (needs provider auth)\n"
        "python . --dry-run    # wiring check, no LLM calls, no auth\n"
        "python . -v           # also print node lifecycle events\n"
        "```\n"
        "\n"
        "## Requirements\n"
        "\n"
        f"{reqs}\n"
    )


def _download_wheels(requirements: list[str], wheels_dir: Path) -> None:
    """Download the third-party wheels into *wheels_dir* for offline install.

    Uses ``python -m pip download``. Raises a clear error if pip is unavailable
    (e.g. a uv-managed venv ships no pip) — run ``--offline`` from an environment
    that has pip, or drop ``--offline`` and let the target ``pip install`` fetch
    the (few, pure-Python) deps at install time.
    """
    if importlib.util.find_spec("pip") is None:
        raise RuntimeError(
            "--offline needs pip in the current environment to download wheels, "
            "but pip is not importable here. Re-run --offline from an environment "
            "with pip installed, or omit --offline (the bundle's requirements.txt "
            "still pins httpx/pydantic for a normal `pip install`)."
        )
    wheels_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--dest", str(wheels_dir), *requirements],
        check=True,
    )


def build_bundle(wf: WorkflowDef, out_dir: Path, *, offline: bool = False) -> Path:
    """Write a self-contained bundle for *wf* into *out_dir*; return *out_dir*.

    Overwrites *out_dir* if it exists. With *offline* the third-party wheels are
    downloaded into ``_vendor/wheels/`` for a no-network install.
    """
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    vendor_dir = out_dir / "_vendor"
    vendor_dir.mkdir(parents=True)

    # 1) The generated workflow module.
    (out_dir / "workflow.py").write_text(generate(wf), encoding="utf-8")

    # 2) Vendored package sources.
    for name in _VENDORED_PACKAGES:
        _copy_package(name, vendor_dir)

    # 3) Entry point.
    (out_dir / "__main__.py").write_text(_render_main(), encoding="utf-8")

    # 4) Pinned requirements.
    requirements = [_pin(d) for d in _THIRD_PARTY]
    (out_dir / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")

    # 5) Optional offline wheels.
    if offline:
        _download_wheels(requirements, vendor_dir / "wheels")

    # 6) README.
    (out_dir / "README.md").write_text(_render_readme(wf, requirements, offline), encoding="utf-8")

    return out_dir
