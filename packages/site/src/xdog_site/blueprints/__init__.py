"""Blueprint registration for the x-dog site."""

from __future__ import annotations

from flask import Flask

from xdog_site.blueprints.blog import bp as blog_bp
from xdog_site.blueprints.main import bp as main_bp
from xdog_site.blueprints.seo import bp as seo_bp


def init_bp(app: Flask) -> None:
    """Register all site blueprints on *app*."""
    app.register_blueprint(main_bp)
    app.register_blueprint(blog_bp)
    app.register_blueprint(seo_bp)
