#!/usr/bin/env python3
"""guards CLI — run the deterministic content/feature guards under the depins venv.

The guards import depins' live registries (flask/flask-babel/tinydb), so they must
run in the *depins* virtualenv, not the flow/xdog one.  The flow ``guards`` script
node shells out to this file with ``<depins-venv>/python guards_cli.py`` and feeds a
JSON job on stdin::

    {"repo": "...", "diff": "...", "is_feature": bool, "id": "...",
     "known": {"projects": [...], "slugs": [...], "features": [...]}}

It prints a single JSON line: ``{"ok", "reasons", "kind", "id"}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # guards.py sibling

import guards  # noqa: E402


def main() -> int:
    job = json.loads(sys.stdin.read() or "{}")
    repo = job.get("repo", "")
    diff = job.get("diff", "")
    known = job.get("known", {})
    if job.get("is_feature"):
        res = guards.check_feature(repo, diff, job.get("id", ""), set(known.get("features", [])))
        out = {"ok": res.ok, "reasons": res.reasons, "kind": "feature", "id": res.feature_id}
    else:
        res = guards.check(repo, diff, set(known.get("projects", [])), set(known.get("slugs", [])))
        out = {
            "ok": res.ok,
            "reasons": res.reasons,
            "kind": "project" if res.new_project_key else "blog",
            "id": res.new_project_key or res.new_slug,
        }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
