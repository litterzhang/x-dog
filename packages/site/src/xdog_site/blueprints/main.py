"""Main blueprint: home, packages, package sub-pages, and FAQ."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, render_template
from markupsafe import Markup

from xdog_site.content.faq import FAQS
from xdog_site.content.flow import DESIGN_SECTIONS, EXAMPLES, FEATURES, GAPS, ROADMAP
from xdog_site.content.packages import LAYERS, PACKAGES, PACKAGES_BY_NAME

bp = Blueprint("main", __name__)


@bp.route("/")
def home() -> str:
    layers = [(title, [PACKAGES_BY_NAME[name] for name in names]) for title, names in LAYERS]
    return render_template("index.html", packages=PACKAGES, layers=layers)


@bp.route("/packages")
def packages() -> str:
    return render_template("packages/index.html", packages=PACKAGES)


# -- flow deep-dive sub-pages (registered before the generic <name> route) ----


@bp.route("/packages/flow/design")
def flow_design() -> str:
    return render_template(
        "packages/flow/design.html", pkg=PACKAGES_BY_NAME["flow"], sections=DESIGN_SECTIONS, tab="design"
    )


@bp.route("/packages/flow/features")
def flow_features() -> str:
    return render_template(
        "packages/flow/features.html", pkg=PACKAGES_BY_NAME["flow"], features=FEATURES, tab="features"
    )


@bp.route("/packages/flow/examples")
def flow_examples() -> str:
    return render_template(
        "packages/flow/examples.html", pkg=PACKAGES_BY_NAME["flow"], examples=_flow_examples(), tab="examples"
    )


@bp.route("/packages/flow/roadmap")
def flow_roadmap() -> str:
    return render_template(
        "packages/flow/roadmap.html", pkg=PACKAGES_BY_NAME["flow"], gaps=GAPS, roadmap=ROADMAP, tab="roadmap"
    )


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
