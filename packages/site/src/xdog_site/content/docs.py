"""Shared content model for the dynamic per-package sub-pages (Features + Roadmap).

Every package's static pages (Overview / Design / Reference / Examples) are
authored as markdown under ``content/pages/<package>/`` and rendered by
:mod:`xdog_site.content.docpages`. The two DYNAMIC pages — Features and Roadmap —
keep their content in Python here, so all packages (including flow) share one
:class:`PackageDocs` value plus the generic templates
``templates/packages/docs/{features,roadmap}.html``.

Everything here is hand-written prose kept accurate against each package's actual
source, so the site has no import-time dependency on the packages it documents.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    """A single capability with a one-line explanation, grouped by category."""

    title: str
    detail: str
    category: str = ""


@dataclass(frozen=True)
class Phase:
    """A roadmap phase. ``done`` marks shipped work; unset marks planned work."""

    tag: str
    title: str
    items: tuple[str, ...]
    done: bool = False


@dataclass(frozen=True)
class PackageDocs:
    """The Features + Roadmap content for one package (the dynamic sub-pages)."""

    name: str

    features_intro: str
    feature_categories: tuple[str, ...]
    features: tuple[Feature, ...]

    roadmap_intro: str
    roadmap: tuple[Phase, ...]

    def grouped_features(self) -> tuple[tuple[str, tuple[Feature, ...]], ...]:
        """Features bucketed by ``feature_categories`` order (empty buckets dropped)."""
        out: list[tuple[str, tuple[Feature, ...]]] = []
        for cat in self.feature_categories:
            feats = tuple(f for f in self.features if f.category == cat)
            if feats:
                out.append((cat, feats))
        return tuple(out)
