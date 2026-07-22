"""Main blueprint: home, packages, and FAQ pages."""

from __future__ import annotations

from flask import Blueprint, abort, render_template

from xdog_site.content.faq import FAQS
from xdog_site.content.packages import LAYERS, PACKAGES, PACKAGES_BY_NAME

bp = Blueprint("main", __name__)


@bp.route("/")
def home() -> str:
    layers = [(title, [PACKAGES_BY_NAME[name] for name in names]) for title, names in LAYERS]
    return render_template("index.html", packages=PACKAGES, layers=layers)


@bp.route("/packages")
def packages() -> str:
    return render_template("packages/index.html", packages=PACKAGES)


@bp.route("/packages/<name>")
def package_detail(name: str) -> str:
    pkg = PACKAGES_BY_NAME.get(name)
    if pkg is None:
        abort(404)
    return render_template("packages/detail.html", pkg=pkg)


@bp.route("/faq")
def faq() -> str:
    return render_template("faq.html", faqs=FAQS)
