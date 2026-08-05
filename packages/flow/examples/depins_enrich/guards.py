"""Deterministic content guards for the enrichment cycle.

These run AFTER the builder agent has edited the working tree but BEFORE any
commit. They enforce the "exactly one small, well-formed, non-duplicate change"
invariant by diffing the live registries against the ledger of already-known
keys/slugs and validating the new entry's shape.

Contract:
    check(cfg) -> GuardResult
where GuardResult carries: ok, reasons (list[str]), new_project_key, new_slug.

The guards import the LIVE registries from the repo (the builder edited those
Python files in place), so an added project/article shows up as a set-difference
against the ledger. This also naturally rejects "no-op" cycles (0 additions) and
"runaway" cycles (>1 addition of a kind, or additions of both kinds at once when
that is disallowed).
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# --- Allowed enumerations (bounded to keep content sane) ---
_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HTTPS_RE = re.compile(r"^https://[a-z0-9.-]+(?:/[^\s\"'<>]*)?$", re.IGNORECASE)

_ALLOWED_CATEGORIES = {
    "Bandwidth", "Compute", "Storage", "Sensor", "Wireless",
    "Energy", "Mobility", "AI", "Server", "Connectivity",
}
_ALLOWED_STATUS = {"Pending", "Active", "Supported", "Beta"}
# Device tokens are constrained to those with an icon asset (static/img/<d>.svg).
_ALLOWED_DEVICES = {"windows", "macos", "linux", "chrome", "android", "ios"}

# Characters we refuse to see in newly-added *content* lines of the diff — these
# indicate raw HTML / template injection rather than plain user-facing strings.
_DANGEROUS = ("<script", "</", "{{", "}}", "{%", "%}", "javascript:", "onerror=", "onload=")

# For FEATURE cycles the builder legitimately edits Jinja templates, so the
# template control tokens ({{ }} {% %} and closing tags) are expected. We still
# refuse the genuinely dangerous ones: inline event handlers, javascript: URIs,
# and <script> with an external src (an exfiltration/inject vector). An inline
# <script>…</script> block is still refused outright — features here are HTML +
# server logic, never client JS.
_DANGEROUS_FEATURE = ("javascript:", "onerror=", "onload=", "onclick=", "onmouseover=", "<script")


@dataclass
class GuardResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    new_project_key: str | None = None
    new_slug: str | None = None


def _load_ledger(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _live_projects(repo_src: Path):
    """Import the live project registry fresh (builder edited it on disk)."""
    import sys

    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    mod = importlib.import_module("depins.db_parts.projects")
    importlib.reload(mod)
    return mod._PROJECTS  # type: ignore[attr-defined]


def _live_articles(repo_src: Path):
    import sys

    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    mod = importlib.import_module("depins.blueprints.blog")
    importlib.reload(mod)
    return mod.ARTICLES  # type: ignore[attr-defined]



# Registry source files whose *members* define catalog content. A feature cycle
# may edit these files freely — routes, rendering and helpers live there too —
# but it may not add a member.
_BLOG_SRC = "src/depins/blueprints/blog.py"
_PROJECTS_SRC = "src/depins/db_parts/projects.py"


def _file_at_head(repo: Path, rel_path: str) -> str | None:
    """The file's contents at git HEAD — the state before the builder edited it."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{rel_path}"],
        capture_output=True, text=True, timeout=60,
    )
    return proc.stdout if proc.returncode == 0 else None


def _registry_ids(source: str, target: str, ident: str) -> set[str] | None:
    """Identifiers declared by the ``target`` list literal in *source*.

    Parses rather than pattern-matches: entries are built with ``_("...")`` and
    ``datetime(...)`` calls, so the literal cannot be eval'd, but the identifying
    field is always a plain string. Returns None when the shape is not what we
    expect, so the caller can say so instead of silently reporting "no members".

    ``ident`` is ``"slug"`` for the blog's list-of-dicts and ``"key"`` for the
    project list's ``Project(key=...)`` calls.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    node = None
    for stmt in ast.walk(tree):
        targets = (
            [stmt.target] if isinstance(stmt, ast.AnnAssign)
            else list(stmt.targets) if isinstance(stmt, ast.Assign)
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == target for t in targets):
            node = stmt.value
            break
    if not isinstance(node, ast.List):
        return None

    found: set[str] = set()
    for element in node.elts:
        if isinstance(element, ast.Dict):
            for key, value in zip(element.keys, element.values, strict=False):
                if (
                    isinstance(key, ast.Constant) and key.value == ident
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)
                ):
                    found.add(value.value)
        elif isinstance(element, ast.Call):
            for kw in element.keywords:
                if kw.arg == ident and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


def _added_registry_members(
    repo: Path, live: set[str], rel_path: str, target: str, ident: str
) -> tuple[set[str], str | None]:
    """Members present now but absent at HEAD, or an explanation of why we cannot tell.

    The parser is validated against the live registry before its answer is used:
    if reading the *current* file does not reproduce what the app actually loaded,
    it does not understand this source and must not be trusted to say what is new.
    """
    current_source = (repo / rel_path).read_text(encoding="utf-8")
    parsed_now = _registry_ids(current_source, target, ident)
    if parsed_now is None or parsed_now != live:
        return set(), f"cannot read {target} from {rel_path} (parser disagrees with the loaded registry)"
    head_source = _file_at_head(repo, rel_path)
    if head_source is None:
        return set(), f"cannot read {rel_path} at HEAD to compare against"
    before = _registry_ids(head_source, target, ident)
    if before is None:
        return set(), f"cannot read {target} from {rel_path} at HEAD"
    return live - before, None


def _scan_diff_for_dangerous(diff_text: str, tokens: tuple[str, ...] = _DANGEROUS) -> list[str]:
    bad: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        low = line.lower()
        for tok in tokens:
            if tok in low:
                bad.append(f"diff adds disallowed token {tok!r}: {line.strip()[:120]}")
    return bad


def check(
    repo_dir: str | Path,
    diff_text: str,
    known_keys: set[str],
    known_slugs: set[str],
) -> GuardResult:
    repo_dir = Path(repo_dir)
    repo_src = repo_dir / "src"
    templates = repo_dir / "src" / "depins" / "templates" / "projects"
    reasons: list[str] = []

    # --- Discover additions vs ledger ---
    projects = _live_projects(repo_src)
    articles = _live_articles(repo_src)

    live_keys = {p.key for p in projects}
    live_slugs = {a["slug"] for a in articles}

    new_keys = sorted(live_keys - known_keys)
    new_slugs = sorted(live_slugs - known_slugs)

    total_new = len(new_keys) + len(new_slugs)
    if total_new == 0:
        return GuardResult(ok=False, reasons=["no new project or blog slug detected (no-op cycle)"])
    if len(new_keys) > 1:
        reasons.append(f"more than one new project: {new_keys}")
    if len(new_slugs) > 1:
        reasons.append(f"more than one new blog slug: {new_slugs}")
    if new_keys and new_slugs:
        reasons.append(f"cycle added both a project ({new_keys}) and a blog ({new_slugs}); only one allowed")

    new_project_key = new_keys[0] if len(new_keys) == 1 else None
    new_slug = new_slugs[0] if len(new_slugs) == 1 else None

    # --- Validate a new project ---
    if new_project_key is not None:
        proj = next((p for p in projects if p.key == new_project_key), None)
        if proj is None:
            reasons.append(f"internal: new key {new_project_key} vanished")
        else:
            if not _KEY_RE.match(proj.key):
                reasons.append(f"project key {proj.key!r} fails slug regex")
            if not proj.name.strip():
                reasons.append("project name empty")
            if proj.category not in _ALLOWED_CATEGORIES:
                reasons.append(f"category {proj.category!r} not in {sorted(_ALLOWED_CATEGORIES)}")
            if proj.status not in _ALLOWED_STATUS:
                reasons.append(f"status {proj.status!r} not in {sorted(_ALLOWED_STATUS)}")
            if not _HTTPS_RE.match(proj.website or ""):
                reasons.append(f"website {proj.website!r} not a bare https URL")
            if not proj.devices:
                reasons.append("project devices list is empty (field is required)")
            else:
                bad_dev = [d for d in proj.devices if d.lower() not in _ALLOWED_DEVICES]
                if bad_dev:
                    reasons.append(f"unknown device tokens {bad_dev}; allowed {sorted(_ALLOWED_DEVICES)}")
            if not (proj.introduction or "").strip():
                reasons.append("project introduction empty")
            # Required per-project template
            tpl = templates / f"project_{proj.key}.html"
            if not tpl.exists():
                reasons.append(f"missing required template {tpl.relative_to(repo_dir)}")

    # --- Validate a new blog article ---
    if new_slug is not None:
        art = next((a for a in articles if a["slug"] == new_slug), None)
        if art is None:
            reasons.append(f"internal: new slug {new_slug} vanished")
        else:
            if not _SLUG_RE.match(art["slug"]):
                reasons.append(f"slug {art['slug']!r} fails slug regex")
            if not str(art.get("title", "")).strip():
                reasons.append("blog title empty")
            if not str(art.get("description", "")).strip():
                reasons.append("blog description empty")
            body = art.get("body") or []
            if not isinstance(body, list) or len(body) < 2:
                reasons.append("blog body must be a list of >=2 paragraphs")
            tags = art.get("tags") or []
            if not isinstance(tags, list) or not tags:
                reasons.append("blog tags must be a non-empty list")
            if art.get("date") is None:
                reasons.append("blog date missing")

    # --- Diff-level safety: no raw HTML/template injection in added content ---
    reasons.extend(_scan_diff_for_dangerous(diff_text))

    return GuardResult(
        ok=(len(reasons) == 0),
        reasons=reasons,
        new_project_key=new_project_key,
        new_slug=new_slug,
    )


@dataclass
class FeatureResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    feature_id: str | None = None


def check_feature(
    repo_dir: str | Path,
    diff_text: str,
    feature_id: str,
    known_features: set[str],
) -> FeatureResult:
    """Deterministic guard for a FEATURE cycle (not a content addition).

    A feature must:
      * declare a NEW, well-formed feature id (not already in the ledger);
      * NOT smuggle in a new project or blog slug (those go through ``check``) —
        the live registries must be unchanged vs their own current contents, so
        a feature cycle cannot also mutate catalog data;
      * pass a Jinja-aware safety scan (template control tokens are allowed since
        features edit templates, but inline JS / event handlers / javascript:
        URIs are still refused).

    Registry/route/200 correctness is still enforced by the deterministic boot
    sweep in orchestrate (which also sweeps the feature's declared new routes).
    """
    repo_dir = Path(repo_dir)
    repo_src = repo_dir / "src"
    reasons: list[str] = []

    fid = (feature_id or "").strip()
    if not fid:
        return FeatureResult(ok=False, reasons=["feature manifest missing 'id'"])
    if not _SLUG_RE.match(fid):
        reasons.append(f"feature id {fid!r} fails slug regex (lowercase a-z0-9 and dashes)")
    if fid in known_features:
        reasons.append(f"feature id {fid!r} already in ledger (duplicate)")

    # A feature must not also add catalog content — that is what check() is for.
    try:
        projects = _live_projects(repo_src)
        articles = _live_articles(repo_src)
        live_keys = {p.key for p in projects}
        live_slugs = {a["slug"] for a in articles}
    except Exception as exc:  # noqa: BLE001
        return FeatureResult(ok=False, reasons=[f"could not import live registries: {exc}"])

    # Compare the registries against their own state at HEAD. The previous check
    # scanned the diff for added lines beginning with `"slug"` / `key=`, which
    # cannot tell a new article from feature code that happens to build a dict
    # carrying a slug — a false positive that rejected legitimate features for
    # merely touching blog.py, where the routes also live.
    for live, rel_path, target, ident in (
        (live_slugs, _BLOG_SRC, "ARTICLES", "slug"),
        (live_keys, _PROJECTS_SRC, "_PROJECTS", "key"),
    ):
        added, problem = _added_registry_members(repo_dir, live, rel_path, target, ident)
        if problem:
            reasons.append(problem)
        elif added:
            reasons.append(
                f"feature cycle adds catalog {ident}(s) {sorted(added)} to {rel_path}; "
                f"keep features and content separate"
            )

    reasons.extend(_scan_diff_for_dangerous(diff_text, tokens=_DANGEROUS_FEATURE))

    return FeatureResult(ok=(len(reasons) == 0), reasons=reasons, feature_id=fid)


