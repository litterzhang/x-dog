"""Blog articles, authored as markdown files under ``content/pages/blog/``.

Each ``<slug>.md`` carries YAML frontmatter (title, description, date, tags) and
a markdown body. Articles are loaded and rendered on demand and cached by the
directory's mtime, so adding or editing a post needs no code change or restart.

The public surface (:class:`Article`, :data:`ARTICLES`, :data:`ARTICLES_BY_SLUG`)
is kept dynamic via module ``__getattr__`` so importers always see the current
set — mirroring the reference ``huge-blog`` loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from markupsafe import Markup
from xdog.site.content.docpages import render_markdown

_BLOG_DIR = Path(__file__).resolve().parent / "pages" / "blog"

# Default post author when a markdown file's frontmatter omits ``author``.
_DEFAULT_AUTHOR = "xdog"


@dataclass(frozen=True)
class Article:
    """One rendered blog article."""

    slug: str
    title: str
    description: str
    body: Markup  # rendered HTML
    date: datetime
    tags: tuple[str, ...]
    author: str = _DEFAULT_AUTHOR


# Cache keyed by the directory mtime so new/edited/removed posts are picked up.
_cache: tuple[float, list[Article]] | None = None


def _parse_date(raw: Any) -> datetime:
    """Coerce a frontmatter ``date`` (datetime or ISO/space string) to a datetime."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip())
        except ValueError:
            pass
    return datetime.min


def _load_articles() -> list[Article]:
    """Parse every ``pages/blog/*.md`` into an Article, newest first."""
    if not _BLOG_DIR.is_dir():
        return []
    articles: list[Article] = []
    for path in _BLOG_DIR.glob("*.md"):
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        raw_tags = post.get("tags") or []
        tags = tuple(str(t) for t in raw_tags) if isinstance(raw_tags, list) else ()
        articles.append(
            Article(
                slug=path.stem,
                title=str(post.get("title") or path.stem),
                description=str(post.get("description") or ""),
                body=render_markdown(post.content),
                date=_parse_date(post.get("date")),
                tags=tags,
                author=str(post.get("author") or _DEFAULT_AUTHOR),
            )
        )
    articles.sort(key=lambda a: a.date, reverse=True)  # newest first
    return articles


def get_articles() -> list[Article]:
    """Return all articles (newest first), cached by the blog directory mtime."""
    global _cache
    try:
        mtime = _BLOG_DIR.stat().st_mtime
    except OSError:
        return []
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    articles = _load_articles()
    _cache = (mtime, articles)
    return articles


def get_article(slug: str) -> Article | None:
    """Return the article with *slug*, or None."""
    return next((a for a in get_articles() if a.slug == slug), None)


def __getattr__(name: str) -> Any:
    """Expose ARTICLES / ARTICLES_BY_SLUG as always-current module attributes."""
    if name == "ARTICLES":
        return get_articles()
    if name == "ARTICLES_BY_SLUG":
        return {a.slug: a for a in get_articles()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
