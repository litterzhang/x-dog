"""Main blueprint: home, packages, package sub-pages, and FAQ."""

from __future__ import annotations

import functools
import json as jsonlib
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request
from markupsafe import Markup

from xdog_site.content.faq import FAQS
from xdog_site.content.flow import DESIGN_SECTIONS, EXAMPLES, FEATURES, GAPS, ROADMAP
from xdog_site.content.packages import LAYERS, PACKAGES, PACKAGES_BY_NAME

bp = Blueprint("main", __name__)

_MAX_UPLOAD_BYTES = 256 * 1024  # generous cap for a workflow JSON


@bp.route("/")
def home() -> str:
    layers = [(title, [PACKAGES_BY_NAME[name] for name in names]) for title, names in LAYERS]
    return render_template("index.html", packages=PACKAGES, layers=layers)


@bp.route("/packages/all")
def packages() -> str:
    return render_template("packages/index.html", packages=PACKAGES)


# -- flow deep-dive sub-pages (registered before the generic <name> route) ----


@bp.route("/packages/flow/design")
def flow_design() -> str:
    return render_template("packages/flow/design.html", pkg=PACKAGES_BY_NAME["flow"], sections=DESIGN_SECTIONS)


@bp.route("/packages/flow/features")
def flow_features() -> str:
    return render_template("packages/flow/features.html", pkg=PACKAGES_BY_NAME["flow"], features=FEATURES)


@bp.route("/packages/flow/examples")
def flow_examples() -> str:
    return render_template("packages/flow/examples.html", pkg=PACKAGES_BY_NAME["flow"], examples=_flow_examples())


@bp.route("/packages/flow/roadmap")
def flow_roadmap() -> str:
    return render_template("packages/flow/roadmap.html", pkg=PACKAGES_BY_NAME["flow"], gaps=GAPS, roadmap=ROADMAP)


# -- HaveFun: load a workflow, view diagrams, fill inputs, run async ----------

# Curated built-in workflows offered in the HaveFun example picker (also the
# allowlist for the ``example`` load path).  Kept small: a self-contained agent
# example that is cheap to run.
_HAVEFUN_STEMS: tuple[str, ...] = ("agent_calculator", "refine_loop")


@bp.route("/packages/flow/havefun")
def flow_havefun() -> str:
    return render_template(
        "packages/flow/havefun.html", pkg=PACKAGES_BY_NAME["flow"], example_stems=list(_HAVEFUN_STEMS)
    )


def _load_workflow_from_request(data: dict[str, Any]) -> tuple[Any, Path | None, str | None]:
    """Resolve the request body to a (workflow, base_dir, error) tuple.

    A built-in ``example`` stem or an uploaded ``json`` string.  Returns
    ``(None, None, message)`` on any problem.
    """
    from flow.loader import parse_workflow, validate_workflow
    from flow.models import WorkflowDef

    ex_dir = _examples_dir()
    stem = data.get("example")
    if stem:
        if stem not in _HAVEFUN_STEMS or ex_dir is None:
            return None, None, f"Unknown example {stem!r}."
        from flow.loader import load_workflow

        try:
            return load_workflow(ex_dir / f"{stem}.json"), ex_dir, None
        except Exception as exc:  # noqa: BLE001
            return None, None, f"Failed to load example: {exc}"

    raw = data.get("json")
    if not isinstance(raw, str) or not raw.strip():
        return None, None, "Provide an example or a workflow JSON."
    if len(raw.encode("utf-8")) > _MAX_UPLOAD_BYTES:
        return None, None, "Workflow JSON is too large."
    try:
        parsed = jsonlib.loads(raw)
        wf: WorkflowDef = parse_workflow(parsed)
        validate_workflow(wf)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Invalid workflow: {exc}"
    return wf, None, None


@bp.route("/packages/flow/havefun/load", methods=["POST"])
def flow_havefun_load() -> Any:
    from flow.graph import to_ascii_diagram, to_svg

    data = request.get_json(silent=True) or {}
    wf, _base, err = _load_workflow_from_request(data)
    if err is not None:
        return jsonify({"ok": False, "error": err}), 400
    try:
        svg = to_svg(wf)
    except Exception:  # noqa: BLE001
        svg = ""
    ascii_diagram = to_ascii_diagram(wf)
    inputs = [{"name": k, "default": v} for k, v in wf.initial_state]
    return jsonify({"ok": True, "name": wf.name, "svg": svg, "ascii": ascii_diagram, "inputs": inputs})


@bp.route("/packages/flow/havefun/run", methods=["POST"])
def flow_havefun_run() -> Any:
    from xdog_site.jobs import runner

    data = request.get_json(silent=True) or {}
    wf, base_dir, err = _load_workflow_from_request(data)
    if err is not None:
        return jsonify({"ok": False, "error": err}), 400

    raw_inputs = data.get("inputs") or {}
    inputs = {str(k): str(v) for k, v in raw_inputs.items()} if isinstance(raw_inputs, dict) else {}

    try:
        import ai
        from agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

        provider = ai.provider(wf.provider or "copilot")
        base_stream = stream_fn_from_provider(provider)

        def _sf(_model: str) -> Any:
            return base_stream

        def _wsf(model: str) -> Any:
            return web_search_fn_from_provider(provider, model)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Runner unavailable: {exc}"}), 503

    job_id = runner.start(wf, inputs, stream_fn_factory=_sf, base_dir=base_dir, web_search_fn_factory=_wsf)
    if job_id is None:
        return jsonify({"ok": False, "error": "A run is already in progress. Try again shortly."}), 429
    return jsonify({"ok": True, "job_id": job_id})


@bp.route("/packages/flow/havefun/status/<job_id>")
def flow_havefun_status(job_id: str) -> Any:
    from xdog_site.jobs import runner

    job = runner.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job."}), 404
    payload: dict[str, Any] = {"ok": True, "state": job.state, "elapsed": job.elapsed, "log": job.log}
    if job.state == "done":
        payload["result"] = job.result
    elif job.state == "error":
        payload["error"] = job.error
    return jsonify(payload)


@bp.route("/packages/<name>")
def package_detail(name: str) -> str:
    pkg = PACKAGES_BY_NAME.get(name)
    if pkg is None:
        abort(404)
    return render_template("packages/detail.html", pkg=pkg)


@bp.route("/faq")
def faq() -> str:
    return render_template("faq.html", faqs=FAQS)


# -- flow example rendering ---------------------------------------------------


@functools.lru_cache(maxsize=1)
def _examples_dir() -> Path | None:
    """Locate the installed flow package's examples directory, or None."""
    try:
        import flow

        assert flow.__file__ is not None
        candidate = Path(flow.__file__).resolve().parents[2] / "examples"
        return candidate if candidate.is_dir() else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _flow_examples() -> tuple[dict[str, Any], ...]:
    """Render each curated example to SVG + ASCII + one generated-Python sample.

    Every step is guarded so a missing binary or bad example degrades the card
    (skips its SVG) instead of 500-ing the page.  Only the authored allow-list of
    stems is loaded — never user input.  Cached: examples are static content.
    """
    ex_dir = _examples_dir()
    if ex_dir is None:
        return ()
    from flow.codegen import generate
    from flow.graph import to_ascii_diagram, to_svg
    from flow.loader import load_workflow

    out: list[dict[str, Any]] = []
    show_codegen = True
    for meta in EXAMPLES:
        path = ex_dir / f"{meta.stem}.json"
        if not path.is_file():
            continue
        try:
            wf = load_workflow(path)
        except Exception:
            continue
        svg = ascii_diagram = code = None
        try:
            svg = Markup(to_svg(wf))
        except Exception:
            svg = None
        try:
            ascii_diagram = to_ascii_diagram(wf)
        except Exception:
            ascii_diagram = None
        # One generated-Python sample (the first example that compiles cleanly).
        if show_codegen:
            try:
                code = generate(wf)
                show_codegen = False
            except Exception:
                code = None
        out.append(
            {
                "title": meta.title,
                "blurb": meta.blurb,
                "effect": meta.effect,
                "stem": meta.stem,
                "svg": svg,
                "ascii": ascii_diagram,
                "code": code,
            }
        )
    return tuple(out)
