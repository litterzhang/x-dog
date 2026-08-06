"""Blueprint for the two files crawlers ask for: ``/robots.txt`` and ``/sitemap.xml``.

Both are derived from the same registries the routes are, so they cannot drift:
a package added to ``PACKAGES``, a markdown page dropped into ``content/pages``
or a post added to ``content/pages/blog`` shows up here without anyone
remembering to update a list.

A doc sub-page is only listed when its markdown file actually exists —
``render_page`` returns ``None`` otherwise and the route 404s, and a sitemap that
advertises 404s is worse than no sitemap.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from flask import Blueprint, Response
from xdog.site.blueprints.main import HAVEFUN_PACKAGES, PACKAGE_DOCS
from xdog.site.content.blog import get_articles
from xdog.site.content.docpages import STATIC_PAGES, render_page
from xdog.site.content.packages import PACKAGES

bp = Blueprint("seo", __name__)

# The canonical origin. Deriving it from the request would emit whatever host and
# scheme the proxy happened to pass through — and this site sits behind nginx with
# no ProxyFix, so that is ``http://127.0.0.1:8080`` as often as not.
_DEFAULT_BASE_URL = "https://xdog.942295.xyz"

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# IndexNow verification key. Public by design — it proves control of the origin by
# being retrievable from it, so committing it is the point, not a leak. Rotating
# it is just changing this constant.
_INDEXNOW_KEY = "443184433fb36a975f9d590d0b75b2e3"

# Endpoints a crawler must not follow: the HaveFun runner's job-status polling is
# a GET, unbounded in cardinality, and meaningless without the job it belongs to.
_DISALLOWED = ("/havefun/*/status/", "/havefun/*/load", "/havefun/*/run")


def _base_url() -> str:
    """Canonical origin, overridable for a staging host or a local check."""
    return os.environ.get("XDOG_SITE_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _entries() -> list[tuple[str, datetime | None, str]]:
    """Every crawlable URL as ``(path, lastmod, changefreq)``, in reading order."""
    entries: list[tuple[str, datetime | None, str]] = [
        ("/", None, "weekly"),
        ("/packages/all", None, "weekly"),
        ("/faq", None, "monthly"),
        ("/blog", None, "weekly"),
    ]

    articles = get_articles()
    for article in articles:
        entries.append((f"/blog/{article.slug}", article.date, "yearly"))

    for package in PACKAGES:
        entries.append((f"/packages/{package.name}", None, "weekly"))
        for page in STATIC_PAGES:
            if page == "overview":
                continue  # same content as /packages/<name>; listing both splits the signal
            if render_page(package.name, page) is not None:
                entries.append((f"/packages/{package.name}/{page}", None, "monthly"))
        if package.name in PACKAGE_DOCS:
            entries.append((f"/packages/{package.name}/features", None, "monthly"))
            entries.append((f"/packages/{package.name}/roadmap", None, "monthly"))

    entries.extend((f"/havefun/{name}", None, "monthly") for name in HAVEFUN_PACKAGES)
    return entries


@bp.route(f"/{_INDEXNOW_KEY}.txt")
def indexnow_key() -> Response:
    """Serve the IndexNow key at the origin root, which is how ownership is proven.

    IndexNow replaced the sitemap ping endpoints Google (404) and Bing (410) both
    retired: instead of announcing that a sitemap changed, you tell the engines
    which URLs changed, and they trust you because this file answers from the same
    origin. One submission reaches Bing, Yandex, Seznam and Naver.
    """
    return Response(_INDEXNOW_KEY, mimetype="text/plain")


@bp.route("/robots.txt")
def robots() -> Response:
    """Allow everything worth indexing, and point at the sitemap."""
    base = _base_url()
    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {path}" for path in _DISALLOWED]
    lines += ["", f"Sitemap: {base}/sitemap.xml", ""]
    return Response("\n".join(lines), mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap() -> Response:
    """A urlset built from the live route registries."""
    base = _base_url()
    root = ET.Element("urlset", {"xmlns": _SITEMAP_NS})
    for path, lastmod, changefreq in _entries():
        url = ET.SubElement(root, "url")
        ET.SubElement(url, "loc").text = f"{base}{path}"
        if lastmod is not None:
            stamp = lastmod if lastmod.tzinfo else lastmod.replace(tzinfo=UTC)
            ET.SubElement(url, "lastmod").text = stamp.date().isoformat()
        ET.SubElement(url, "changefreq").text = changefreq
    body = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return Response(body, mimetype="application/xml")
