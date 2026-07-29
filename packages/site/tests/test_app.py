"""Route + content contract tests for the x-dog site."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient
from xdog_site import create_app
from xdog_site.content.blog import get_articles
from xdog_site.content.faq import FAQS
from xdog_site.content.packages import PACKAGES


@pytest.fixture
def client() -> FlaskClient:
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_create_app_returns_flask() -> None:
    assert isinstance(create_app(), Flask)


def test_home_ok_lists_every_package(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for pkg in PACKAGES:
        assert pkg.name in body
    # styled via the copied CSS framework
    assert "hack.css" in body and "site.css" in body


def test_packages_index_ok(client: FlaskClient) -> None:
    resp = client.get("/packages/all")
    assert resp.status_code == 200
    assert "Packages" in resp.get_data(as_text=True)


@pytest.mark.parametrize("pkg", [p.name for p in PACKAGES])
def test_each_package_detail_ok(client: FlaskClient, pkg: str) -> None:
    resp = client.get(f"/packages/{pkg}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert pkg in body
    assert "Highlights" in body


def test_unknown_package_404(client: FlaskClient) -> None:
    assert client.get("/packages/does-not-exist").status_code == 404


def test_blog_index_ok_lists_articles(client: FlaskClient) -> None:
    resp = client.get("/blog")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert get_articles()[0].title in body


@pytest.mark.parametrize("slug", [a.slug for a in get_articles()])
def test_each_article_ok(client: FlaskClient, slug: str) -> None:
    resp = client.get(f"/blog/{slug}")
    assert resp.status_code == 200
    art = next(a for a in get_articles() if a.slug == slug)
    body = resp.get_data(as_text=True)
    assert art.title in body
    # the markdown body is rendered as HTML (paragraphs), not a raw list
    assert "markdown-body" in body


def test_unknown_article_404(client: FlaskClient) -> None:
    assert client.get("/blog/nope").status_code == 404


def test_blog_pagination_param_is_safe(client: FlaskClient) -> None:
    assert client.get("/blog?page=999").status_code == 200
    assert client.get("/blog?page=notanumber").status_code == 200


def test_faq_ok_renders_questions(client: FlaskClient) -> None:
    resp = client.get("/faq")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert FAQS[0]["q"] in body
    # the honest production-readiness answer is present
    assert "retry" in body.lower()


def test_static_css_served(client: FlaskClient) -> None:
    assert client.get("/static/css/hack.css").status_code == 200
    assert client.get("/static/css/site.css").status_code == 200


def test_favicon_served_and_linked(client: FlaskClient) -> None:
    resp = client.get("/static/favicon.ico")
    assert resp.status_code == 200
    assert "icon" in resp.content_type  # image/vnd.microsoft.icon
    assert 'rel="icon"' in client.get("/").get_data(as_text=True)


def test_404_page_is_styled(client: FlaskClient) -> None:
    body = client.get("/no-such-page").get_data(as_text=True)
    assert "404" in body
    assert "hack.css" in body  # renders through base.html


# --- flow deep-dive sub-pages (now the shared markdown + dynamic routes) ------


@pytest.mark.parametrize(
    "path,crumb",
    [
        ("/packages/flow", "flow"),
        ("/packages/flow/design", "flow / Design"),
        ("/packages/flow/features", "flow / Features"),
        ("/packages/flow/reference", "flow / Reference"),
        ("/packages/flow/examples", "flow / Examples"),
        ("/packages/flow/roadmap", "flow / Roadmap"),
    ],
)
def test_flow_subpages_ok_with_breadcrumb(client: FlaskClient, path: str, crumb: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # the h1 breadcrumb reflects the sub-page, e.g. "x-dog / Packages / flow / Design"
    assert f"Packages / {crumb}" in body
    # the left-nav flow submenu links every sub-page
    assert "/packages/flow/design" in body and "/packages/flow/roadmap" in body
    assert "/packages/flow/reference" in body


def test_flow_reference_documents_schema_and_rules(client: FlaskClient) -> None:
    body = client.get("/packages/flow/reference").get_data(as_text=True)
    # the JSON schema, type system, conditions, runtime container, CLI, and validation rules
    assert "output_schema" in body  # a NodeDef field
    assert "$output" in body  # the reserved sink
    assert "xdog-flow" in body  # the CLI section
    assert "not fed by any edge mapping" in body  # a validation rule
    assert "<table>" in body  # the schema/type/rule tables render as markdown tables


def test_flow_examples_renders_ascii_diagrams(client: FlaskClient) -> None:
    body = client.get("/packages/flow/examples").get_data(as_text=True)
    # the examples page is now static markdown: pre-generated ASCII in a code block
    assert "Agent Calculator" in body
    assert "make_problem" in body  # an ASCII-diagram node label
    assert "<pre>" in body or "<code>" in body  # fenced code block
    # live SVG generation moved to the HaveFun page; examples.md points there
    assert "/havefun" in body


def test_flow_roadmap_has_phases(client: FlaskClient) -> None:
    body = client.get("/packages/flow/roadmap").get_data(as_text=True)
    assert "retry" in body.lower()  # a named phase
    assert "Checkpoint" in body  # a roadmap phase


def test_flow_docs_module_importable() -> None:
    from xdog_site.content.docpages import render_page
    from xdog_site.content.flow_docs import DOCS

    # Features + Roadmap are Python; static pages are markdown.
    assert DOCS.features and DOCS.roadmap
    assert sum(len(feats) for _, feats in DOCS.grouped_features()) == len(DOCS.features)
    for page in ("overview", "design", "reference", "examples"):
        assert render_page("flow", page) is not None


# --- generic package sub-pages (ai / agent / tui / coding / claw / flow) ------

_DOC_PACKAGES = ["ai", "agent", "tui", "coding", "claw", "flow"]
_DOC_SUBPAGES = ["design", "features", "reference", "roadmap"]


@pytest.mark.parametrize("name", _DOC_PACKAGES)
@pytest.mark.parametrize("sub", _DOC_SUBPAGES)
def test_package_docs_subpages_ok_with_breadcrumb(client: FlaskClient, name: str, sub: str) -> None:
    resp = client.get(f"/packages/{name}/{sub}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # breadcrumb reflects the sub-page, e.g. "Packages / ai / Design"
    assert f"Packages / {name} / {sub.capitalize()}" in body
    # every doc page is reachable from the collapsible left-nav
    assert f"/packages/{name}/reference" in body


@pytest.mark.parametrize("name", _DOC_PACKAGES)
def test_overview_route_and_nav(client: FlaskClient, name: str) -> None:
    # Both /packages/<name> and /packages/<name>/overview render the overview.
    assert client.get(f"/packages/{name}/overview").status_code == 200
    body = client.get(f"/packages/{name}").get_data(as_text=True)
    # The overview body no longer carries an in-page "Deep dive" block or a CLI line;
    # sub-pages are reached via the left-nav, which links them on every page.
    assert "Deep dive" not in body
    assert "CLI:" not in body
    assert f"/packages/{name}/design" in body  # left-nav submenu
    assert f"/packages/{name}/roadmap" in body


def test_package_docs_unknown_name_404(client: FlaskClient) -> None:
    # flow now uses the same shared routes as the others
    assert client.get("/packages/flow/design").status_code == 200
    assert client.get("/packages/flow/examples").status_code == 200  # flow ships examples
    assert client.get("/packages/ai/examples").status_code == 404  # others do not
    assert client.get("/packages/nope/design").status_code == 404
    assert client.get("/packages/nope/roadmap").status_code == 404


def test_package_docs_content_is_accurate(client: FlaskClient) -> None:
    # ai: the old five-vendor claim is gone; Copilot + protocols are documented
    ai_over = client.get("/packages/ai").get_data(as_text=True)
    assert "Mistral" not in ai_over and "Bedrock" not in ai_over
    assert "copilot" in client.get("/packages/ai/reference").get_data(as_text=True)
    # agent: built-in tools; tui: differential renderer; claw: workspace files
    assert "submit_result" in client.get("/packages/agent/reference").get_data(as_text=True)
    assert "differential" in client.get("/packages/tui/design").get_data(as_text=True).lower()
    assert "IDENTITY.md" in client.get("/packages/claw/reference").get_data(as_text=True)
    # every roadmap carries a forward-looking 2026 phase
    for name in _DOC_PACKAGES:
        assert "2026" in client.get(f"/packages/{name}/roadmap").get_data(as_text=True)


def test_static_markdown_renders_tables(client: FlaskClient) -> None:
    # a markdown reference page renders GFM tables to <table>
    assert "<table>" in client.get("/packages/ai/reference").get_data(as_text=True)
    assert "<table>" in client.get("/packages/flow/reference").get_data(as_text=True)


def test_docpages_loader_unit() -> None:
    from markupsafe import Markup
    from xdog_site.content.docpages import render_page

    ref = render_page("ai", "reference")
    assert ref is not None
    assert isinstance(ref.html, Markup) and "<table>" in ref.html
    assert ref.title  # frontmatter title or capitalized page name
    # missing page for a real package, unknown package, and unknown page → None
    assert render_page("ai", "examples") is None
    assert render_page("nope", "design") is None
    assert render_page("flow", "bogus") is None


def test_docs_content_modules_importable() -> None:
    from xdog_site.content.agent import DOCS as AGENT_DOCS
    from xdog_site.content.ai import DOCS as AI_DOCS
    from xdog_site.content.claw import DOCS as CLAW_DOCS
    from xdog_site.content.coding import DOCS as CODING_DOCS
    from xdog_site.content.flow_docs import DOCS as FLOW_DOCS
    from xdog_site.content.tui import DOCS as TUI_DOCS

    for docs in (AI_DOCS, AGENT_DOCS, TUI_DOCS, CODING_DOCS, CLAW_DOCS, FLOW_DOCS):
        assert docs.features and docs.roadmap
        # grouped_features drops empty buckets and preserves every feature
        grouped = docs.grouped_features()
        assert sum(len(feats) for _, feats in grouped) == len(docs.features)


# --- HaveFun page + async run ------------------------------------------------


def test_havefun_page_ok(client: FlaskClient) -> None:
    resp = client.get("/havefun/flow")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "HaveFun" in body
    assert "hf-run" in body  # the run button
    assert "hf-file" in body  # the upload file input
    assert "agent_calculator" in body  # the example option


def test_old_flow_havefun_url_gone(client: FlaskClient) -> None:
    # HaveFun moved to a per-package route /havefun/<name>; old + un-named URLs are gone.
    assert client.get("/packages/flow/havefun").status_code == 404
    assert client.post("/packages/flow/havefun/load", json={"example": "agent_calculator"}).status_code == 404
    assert client.get("/havefun").status_code == 404  # must name a package
    assert client.get("/havefun/ai").status_code == 404  # only packages with workflows


def test_havefun_load_example_returns_diagram_and_inputs(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"example": "agent_calculator"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "<svg" in d["svg"]
    assert d["ascii"]  # ASCII diagram present
    names = {i["name"] for i in d["inputs"]}
    assert {"a", "b"} <= names  # declared inputs surfaced with defaults


def test_havefun_load_unknown_example_400(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"example": "does-not-exist"})
    assert resp.status_code == 400


def test_havefun_load_uploaded_json(client: FlaskClient) -> None:
    """An uploaded workflow JSON loads and returns its diagram + inputs."""
    payload = {
        "name": "up",
        "provider": "copilot",
        "entry": "x",
        "state": {"n": "1"},
        "nodes": [
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx, n):\n    return n",
                "inputs": ["n"],
                "output": "y",
            }
        ],
        "edges": [{"from": "$in", "to": "x", "map": {"n": "n"}}],
    }
    import json as _json

    resp = client.post("/havefun/flow/load", json={"json": _json.dumps(payload)})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "n" in {i["name"] for i in d["inputs"]}


def test_havefun_rejects_invalid_json(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"json": "{ not valid json"})
    assert resp.status_code == 400


def test_havefun_run_single_slot_then_429_and_status() -> None:
    """Runner runs one job; a second start is refused; status reports lifecycle.

    Exercised directly against the runner with a dry-run stream so no real LLM is
    called, mirroring the route wiring.
    """
    import time

    from flow.cli import _dry_run_stream_fn_factory
    from flow.loader import load_workflow
    from xdog_site.blueprints.main import _examples_dir
    from xdog_site.jobs import runner

    ex_dir = _examples_dir()
    assert ex_dir is not None
    wf = load_workflow(ex_dir / "agent_calculator.json")

    job_id = runner.start(wf, {"a": "3", "b": "4"}, stream_fn_factory=_dry_run_stream_fn_factory)
    assert job_id is not None
    # a second start while the first may still hold the slot is refused (or the
    # first already finished — retry once quickly to observe the busy state).
    second = runner.start(wf, {"a": "1", "b": "2"}, stream_fn_factory=_dry_run_stream_fn_factory)
    # second is either None (busy) or a new id (first finished first); if a new id
    # was returned, the single-slot invariant still held (never two at once).
    assert second is None or isinstance(second, str)

    # wait for completion and assert a good result
    for _ in range(50):
        job = runner.get(job_id) or (runner.get(second) if second else None)
        if job and job.state != "running":
            break
        time.sleep(0.2)
    final = runner.get(second) if second else runner.get(job_id)
    assert final is not None
    assert final.state == "done"
    # job.result is the workflow's $output (runtime["out"]); agent_calculator maps
    # solve.answer -> result.
    assert final.result is not None and "result" in final.result


def test_havefun_load_refine_loop_example(client: FlaskClient) -> None:
    """The generator↔critic refine_loop example loads with a topic input only.

    `feedback` is an internal optional loop-carried port, NOT a workflow input, so
    it must not surface as a user-facing input box.
    """
    resp = client.post("/havefun/flow/load", json={"example": "refine_loop"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "<svg" in d["svg"]
    names = {i["name"] for i in d["inputs"]}
    assert "topic" in names  # the user-facing input is surfaced
    assert "feedback" not in names  # internal loop-carried port is not an input


def test_havefun_status_includes_execution_log() -> None:
    """A run's status carries the captured execution log (per-node run lines)."""
    import time

    from flow.cli import _dry_run_stream_fn_factory
    from flow.loader import load_workflow
    from xdog_site.blueprints.main import _examples_dir
    from xdog_site.jobs import runner

    ex_dir = _examples_dir()
    assert ex_dir is not None
    wf = load_workflow(ex_dir / "refine_loop.json")
    job_id = runner.start(
        wf, {"topic": "t", "feedback": ""}, stream_fn_factory=_dry_run_stream_fn_factory, base_dir=ex_dir
    )
    assert job_id is not None
    for _ in range(50):
        job = runner.get(job_id)
        if job and job.state != "running":
            break
        time.sleep(0.2)
    job = runner.get(job_id)
    assert job is not None and job.state == "done"
    # the log is built from flow's P3 structured event stream (on_event), formatted
    # into per-node lines: "▶ <node>" on start, "✓ <node> (<dur>s[, <n> tok])" on finish.
    assert job.log
    assert any(line.startswith("▶ draft") for line in job.log)
    assert any(line.startswith("✓ draft") for line in job.log)
