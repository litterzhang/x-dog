"""flow.testing.match — the one deep-subset rule, shared by ``when`` and ``expect``.

Both stub selection and output assertion use the same matcher on purpose: an author
learns one rule, not two.  The rule is deliberately small:

* **object** — every expected key must be present and match recursively; extra keys
  in the actual value are ignored (partial by design, so a case asserts intent
  rather than locking the whole structure).
* **array** — same length, element-wise recursive match.  Length is significant
  because "how many items came out of the fan" is usually the thing under test.
* **scalar** — ``==``, except that ``bool`` only matches ``bool`` (Python's
  ``True == 1`` would otherwise let ``"flag": 1`` silently pass).
"""

from __future__ import annotations


def matches(expected: object, actual: object) -> bool:
    """Whether *actual* satisfies the deep-subset pattern *expected*."""
    return _diff(expected, actual, "") is None


def first_difference(expected: object, actual: object, root: str = "") -> tuple[str, object, object] | None:
    """Locate the deepest differing path: ``(path, expected, actual)`` or ``None``.

    Reporting the innermost mismatch (rather than dumping both whole objects) is
    what makes a failed assertion readable on a large ``$output``.
    """
    return _diff(expected, actual, root)


_MISSING = object()


def _diff(expected: object, actual: object, path: str) -> tuple[str, object, object] | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return (path, expected, actual)
        for key, want in expected.items():
            child = f"{path}.{key}" if path else str(key)
            got = actual.get(key, _MISSING)
            if got is _MISSING:
                return (child, want, None)
            found = _diff(want, got, child)
            if found is not None:
                return found
        return None

    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return (path, expected, actual)
        for i, want in enumerate(expected):
            found = _diff(want, actual[i], f"{path}[{i}]")
            if found is not None:
                return found
        return None

    # bool is a subclass of int, so guard both directions before falling back to ==.
    if isinstance(expected, bool) != isinstance(actual, bool):
        return (path, expected, actual)
    if expected == actual:
        return None
    return (path, expected, actual)
