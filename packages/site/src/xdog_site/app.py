"""xdog_site Flask application factory.

Mirrors the depins app-factory pattern (``create_app()`` + ``init_bp``) but with
no i18n, auth, or DB — content is authored in :mod:`xdog_site.content`.
"""

from __future__ import annotations

import os

from flask import Flask, render_template

from xdog_site.blueprints import init_bp


def create_app() -> Flask:
    """Build and configure the x-dog site Flask app."""
    app = Flask(__name__)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.getenv("XDOG_SITE_SECRET") or "dev-insecure-change-me"
    init_bp(app)

    @app.errorhandler(404)
    def not_found(_e: object) -> tuple[str, int]:
        return render_template("errors/404.html"), 404

    return app


def main() -> None:
    """Run the development server (``xdog-site``)."""
    host = os.getenv("XDOG_SITE_HOST", "127.0.0.1")
    port = int(os.getenv("XDOG_SITE_PORT", "8080"))
    create_app().run(host=host, port=port, debug=bool(os.getenv("XDOG_SITE_DEBUG")))


if __name__ == "__main__":
    main()
