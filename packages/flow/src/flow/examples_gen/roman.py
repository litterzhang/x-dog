"""Conversion between integers and Roman numerals."""

from __future__ import annotations

_VALUES: list[tuple[int, str]] = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def to_roman(n: int) -> str:
    """Convert an integer in the range 1..3999 to a Roman numeral."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("n must be an int")
    if n < 1 or n > 3999:
        raise ValueError("n must be between 1 and 3999")
    result: list[str] = []
    remaining = n
    for value, symbol in _VALUES:
        while remaining >= value:
            result.append(symbol)
            remaining -= value
    return "".join(result)


def from_roman(s: str) -> int:
    """Convert a Roman numeral string to its integer value."""
    if not isinstance(s, str):
        raise ValueError("s must be a str")
    if s == "":
        raise ValueError("empty string is not a valid Roman numeral")

    value = 0
    index = 0
    for amount, symbol in _VALUES:
        while s[index : index + len(symbol)] == symbol:
            value += amount
            index += len(symbol)

    if index != len(s) or value < 1 or value > 3999:
        raise ValueError(f"invalid Roman numeral: {s!r}")
    if to_roman(value) != s:
        raise ValueError(f"invalid Roman numeral: {s!r}")
    return value
