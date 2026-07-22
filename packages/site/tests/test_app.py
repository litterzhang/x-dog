"""Route + content contract tests for the x-dog site."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient
from xdog_site import create_app
from xdog_site.content.blog import ARTICLES
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
    assert ARTICLES[0]["title"] in body


@pytest.mark.parametrize("slug", [a["slug"] for a in ARTICLES])
def test_each_article_ok(client: FlaskClient, slug: str) -> None:
    resp = client.get(f"/blog/{slug}")
    assert resp.status_code == 200
    art = next(a for a in ARTICLES if a["slug"] == slug)
    assert art["title"] in resp.get_data(as_text=True)


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


def test_404_page_is_styled(client: FlaskClient) -> None:
    body = client.get("/no-such-page").get_data(as_text=True)
    assert "404" in body
    assert "hack.css" in body  # renders through base.html


# --- flow deep-dive sub-pages ------------------------------------------------


@pytest.mark.parametrize(
    "path,crumb",
    [
        ("/packages/flow", "flow"),
        ("/packages/flow/design", "flow / Design"),
        ("/packages/flow/features", "flow / Features"),
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


def test_flow_examples_renders_live_svg_and_ascii(client: FlaskClient) -> None:
    body = client.get("/packages/flow/examples").get_data(as_text=True)
    # a real Graphviz-generated inline SVG is embedded
    assert "<svg" in body
    # the ASCII diagram is shown in a <pre> block
    assert "<pre>" in body
    # a known example title is present
    assert "Parallel Diamond" in body


def test_flow_roadmap_has_gaps_and_phases(client: FlaskClient) -> None:
    body = client.get("/packages/flow/roadmap").get_data(as_text=True)
    assert "retry" in body.lower()  # a named gap
    assert "Checkpoint" in body  # a roadmap phase


def test_flow_content_module_importable() -> None:
    from xdog_site.content.flow import DESIGN_SECTIONS, EXAMPLES, FEATURES, GAPS, ROADMAP

    assert DESIGN_SECTIONS and FEATURES and EXAMPLES and GAPS and ROADMAP
