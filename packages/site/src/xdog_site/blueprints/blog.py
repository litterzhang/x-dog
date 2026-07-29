"""Blog blueprint: paginated article list and single-article pages."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from xdog_site.content.blog import Article, get_article, get_articles

bp = Blueprint("blog", __name__)

_PAGE_SIZE = 10


def _paginate(items: list[Article], page: int, size: int) -> tuple[list[Article], int, int]:
    """Return (rows, current_page, total_pages) for a 1-based *page*."""
    total_pages = max(1, (len(items) + size - 1) // size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return items[start : start + size], page, total_pages


@bp.route("/blog")
def blog_index() -> str:
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    rows, page, total_pages = _paginate(get_articles(), page, _PAGE_SIZE)
    return render_template("blog/index.html", articles=rows, page=page, total_pages=total_pages)


@bp.route("/blog/<slug>")
def blog_article(slug: str) -> str:
    article = get_article(slug)
    if article is None:
        abort(404)
    return render_template("blog/article.html", article=article)
