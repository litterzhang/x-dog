"""Shared content model for the per-package deep-dive sub-pages.

The ``flow`` package has its own bespoke content module and templates (it carries
extras like the runnable HaveFun page). The other packages — ai, agent, tui,
coding, claw — share this small set of frozen dataclasses and a single set of
generic templates (``templates/packages/docs/*.html``), so each package only has
to author a :class:`PackageDocs` value.

Everything here is hand-written prose kept accurate against each package's actual
source (public ``__init__`` exports, CLI, and modules), so the site has no
import-time dependency on the packages it documents.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """A titled block of prose lines and optional bullet points (Design page)."""

    heading: str
    body: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feature:
    """A single capability with a one-line explanation, grouped by category."""

    title: str
    detail: str
    category: str = ""


@dataclass(frozen=True)
class RefBlock:
    """A reference block: heading, optional prose/bullets, and an optional table.

    When ``columns`` is set the block renders a table whose header is ``columns``
    and whose body is ``rows`` (each row a tuple the same width as ``columns``).
    Prose (``body``/``bullets``) renders above the table, so one block can explain
    a concept and then tabulate it.
    """

    heading: str
    body: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Phase:
    """A roadmap phase. ``done`` marks shipped work; unset marks planned work."""

    tag: str
    title: str
    items: tuple[str, ...]
    done: bool = False


@dataclass(frozen=True)
class PackageDocs:
    """Everything the four generic sub-pages need for one package."""

    name: str

    design_intro: str
    design_sections: tuple[Section, ...]

    features_intro: str
    feature_categories: tuple[str, ...]
    features: tuple[Feature, ...]

    reference_intro: str
    reference_blocks: tuple[RefBlock, ...]

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
