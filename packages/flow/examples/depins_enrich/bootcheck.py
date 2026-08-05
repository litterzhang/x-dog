"""Deterministic boot-check gate for depins.

Boots the Flask app in-process and GETs every core + dynamically-registered
route (projects and blog entries are discovered from the live registries, so a
newly-added project/article is automatically covered) in each supported locale,
asserting HTTP 200. The sweep is fanned out across all CPU cores STRESS_ROUNDS
times to actually exercise the idle box.

Locale is driven via the ``Accept-Language`` header, which depins' i18n
``_select_locale`` honours (cookie -> Accept-Language -> default) without the
302 redirect that ``?lang=`` triggers.

Exit/return contract:
    run(stress_rounds) -> (ok: bool, failures: list[str])

Failures are human-readable "GET <path> [locale] -> <status>" strings.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# Locales to exercise. ``None`` => no Accept-Language header (default path).
_LOCALES: list[str | None] = [None, "en", "zh"]

# Static core routes that must always return 200.
_STATIC_ROUTES: list[str] = [
    "/",
    "/about",
    "/projects",
    "/blog",
    "/sitemap.xml",
    "/robots.txt",
    "/healthz",
]


def _discover_dynamic_routes() -> list[str]:
    """Pull per-project and per-article routes straight from the live app data."""
    routes: list[str] = []

    # Projects: /projects/<key>
    from depins.db_parts.projects import get_projects  # type: ignore

    for p in get_projects():
        routes.append(f"/projects/{p['key']}")

    # Blog: /blog/<slug>
    from depins.blueprints.blog import ARTICLES  # type: ignore

    for a in ARTICLES:
        routes.append(f"/blog/{a['slug']}")

    return routes


def _extra_routes() -> list[str]:
    """Routes a feature cycle declared it added, injected via ``AE_EXTRA_ROUTES``
    (comma/space separated absolute paths). These are swept for 200 alongside the
    static + registry-discovered routes so a newly-added feature route is proven
    to boot in every locale before the change can land on main.
    """
    raw = os.environ.get("AE_EXTRA_ROUTES", "")
    routes: list[str] = []
    for tok in raw.replace(",", " ").split():
        tok = tok.strip()
        if tok.startswith("/") and tok not in routes:
            routes.append(tok)
    return routes


def _all_targets() -> list[tuple[str, str | None]]:
    routes = _STATIC_ROUTES + _discover_dynamic_routes() + _extra_routes()
    # De-dup while preserving order (a feature route may coincide with a static one)
    seen: set[str] = set()
    uniq = [r for r in routes if not (r in seen or seen.add(r))]
    return [(r, loc) for r in uniq for loc in _LOCALES]


def _hit(client, path: str, locale: str | None) -> tuple[str, str | None, int]:
    headers = {}
    if locale:
        headers["Accept-Language"] = locale
    resp = client.get(path, headers=headers)
    return path, locale, resp.status_code


def run(stress_rounds: int = 8) -> tuple[bool, list[str]]:
    """Boot the app and sweep every route in every locale ``stress_rounds`` times.

    Returns (ok, failures). ``ok`` is True only if every request returned 200.
    """
    from depins.app import create_app  # type: ignore

    app = create_app()
    # Do not let TESTING re-raise; we want a broken template to surface as a
    # 500 response that we can report, not a stack trace that aborts the sweep.
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    targets = _all_targets()
    if not targets:
        return False, ["no routes discovered"]

    workers = max(2, (os.cpu_count() or 2) * 2)
    failures: list[str] = []
    seen_bad: set[str] = set()

    # Each round gets its own test client from a shared app; Flask test clients
    # are cheap and thread-safe enough for independent GETs.
    def _round() -> list[str]:
        client = app.test_client()
        bad: list[str] = []
        for path, locale in targets:
            try:
                _p, _loc, code = _hit(client, path, locale)
            except Exception as exc:  # noqa: BLE001 - report, don't crash the gate
                bad.append(f"GET {path} [{locale or 'default'}] -> EXC {type(exc).__name__}: {exc}")
                continue
            if code != 200:
                bad.append(f"GET {path} [{locale or 'default'}] -> {code}")
        return bad

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_round) for _ in range(max(1, stress_rounds))]
        for fut in as_completed(futs):
            for msg in fut.result():
                if msg not in seen_bad:
                    seen_bad.add(msg)
                    failures.append(msg)

    return (len(failures) == 0), failures


if __name__ == "__main__":  # manual smoke: python lib/bootcheck.py
    import sys

    ok, fails = run(int(os.environ.get("STRESS_ROUNDS", "8")))
    if ok:
        print("BOOTCHECK OK — all routes 200 in all locales")
        sys.exit(0)
    print("BOOTCHECK FAILED:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
