"""Stable identifiers for validation failures.

A caller that wants to react differently to "that port does not exist" and
"those two ports have incompatible types" needs something to branch on. Without
a code it has to pattern-match English, which makes every reworded message a
silent breaking change for it — and rewording messages is something we do
freely, because they are written for humans.

The set is deliberately small. Ninety-nine checks in the loader do not need
ninety-nine codes; what a caller does about a failure falls into far fewer
buckets than the number of ways to reach one. So the axis here is **what the
author has to change**, not where in the loader the check lives:
``UNKNOWN_REFERENCE`` covers a missing node, a missing port and an unknown tool
alike, because the repair is the same shape in all three — fix the name or add
the thing.

These strings are API. Add to them freely; renaming or removing one breaks
every consumer that was the point of having them.
"""
from __future__ import annotations

from typing import Final

# -- Naming and structure ----------------------------------------------------

#: A name that should resolve does not: a node, port, tool or subflow that is
#: referenced but absent. Repair: correct the name, or add what is missing.
UNKNOWN_REFERENCE: Final = "unknown-reference"

#: Two things claim the same name, or a name is reserved by the runtime.
DUPLICATE_OR_RESERVED_ID: Final = "duplicate-or-reserved-id"

#: A field the format does not define. Usually a typo for one that it does.
UNKNOWN_FIELD: Final = "unknown-field"

#: A required field is absent.
MISSING_REQUIRED: Final = "missing-required"

#: A field is present with the wrong JSON shape — a string where an object
#: belongs, a scalar where a list does.
WRONG_SHAPE: Final = "wrong-shape"

#: The shape is right and the value is not: outside a numeric range, or not one
#: of an enumerated set.
INVALID_VALUE: Final = "invalid-value"

# -- Typing ------------------------------------------------------------------

#: An edge connects ports whose declared types cannot carry the same value.
#: Distinct from UNKNOWN_REFERENCE because both ends exist — the repair is a
#: conversion or a schema change, not a rename.
TYPE_MISMATCH: Final = "type-mismatch"

#: A port declares a type or JSON Schema the runtime cannot interpret.
INVALID_SCHEMA: Final = "invalid-schema"

# -- Graph -------------------------------------------------------------------

#: The graph does not hang together: no entry node, an unreachable node, an
#: input port nothing feeds.
GRAPH_INCOMPLETE: Final = "graph-incomplete"

#: One input port is fed by several edges that could both fire, so which value
#: arrives would depend on evaluation order.
AMBIGUOUS_INPUT: Final = "ambiguous-input"

#: A cycle without a bound, a bound without a guard, or loop regions that
#: overlap without nesting. The runtime cannot promise termination.
INVALID_LOOP: Final = "invalid-loop"

#: A fan-out or fan-in edge breaks one of the rules that make parallel
#: expansion well-defined.
INVALID_FANOUT: Final = "invalid-fanout"

# -- Node kinds --------------------------------------------------------------

#: A node carries a field belonging to a different kind — an agent node with
#: `code`, a human node with `prompt`, a script node with both `code` and `run`.
NODE_KIND_CONFLICT: Final = "node-kind-conflict"

#: A script node's `code` does not parse, or its signature disagrees with its
#: declared ports.
INVALID_SCRIPT: Final = "invalid-script"

#: A prompt or condition template refers to a root that will not exist at run
#: time.
INVALID_TEMPLATE: Final = "invalid-template"

#: A subflow reference cannot be resolved, is cyclic, or declares ports that a
#: subflow takes from its child.
INVALID_SUBFLOW: Final = "invalid-subflow"

# -- Runtime configuration ---------------------------------------------------

#: An agent node needs a model provider and the workflow names none.
PROVIDER_REQUIRED: Final = "provider-required"

#: The `schedule` block is not a valid timer or hook specification.
INVALID_SCHEDULE: Final = "invalid-schedule"


#: Every code, for tests and for documentation generators.
ALL_CODES: Final = (
    UNKNOWN_REFERENCE,
    DUPLICATE_OR_RESERVED_ID,
    UNKNOWN_FIELD,
    MISSING_REQUIRED,
    WRONG_SHAPE,
    INVALID_VALUE,
    TYPE_MISMATCH,
    INVALID_SCHEMA,
    GRAPH_INCOMPLETE,
    AMBIGUOUS_INPUT,
    INVALID_LOOP,
    INVALID_FANOUT,
    NODE_KIND_CONFLICT,
    INVALID_SCRIPT,
    INVALID_TEMPLATE,
    INVALID_SUBFLOW,
    PROVIDER_REQUIRED,
    INVALID_SCHEDULE,
)
