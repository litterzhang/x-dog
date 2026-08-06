import pytest
from xdog.flow.examples_gen.roman import from_roman, to_roman


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "I"),
        (4, "IV"),
        (9, "IX"),
        (40, "XL"),
        (90, "XC"),
        (400, "CD"),
        (900, "CM"),
        (3999, "MMMCMXCIX"),
    ],
)
def test_to_roman_known_values(n: int, expected: str) -> None:
    assert to_roman(n) == expected


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("I", 1),
        ("IV", 4),
        ("IX", 9),
        ("XL", 40),
        ("XC", 90),
        ("CD", 400),
        ("CM", 900),
        ("MMMCMXCIX", 3999),
    ],
)
def test_from_roman_known_values(s: str, expected: int) -> None:
    assert from_roman(s) == expected


@pytest.mark.parametrize("n", [0, -1, 4000, 100000])
def test_to_roman_out_of_range(n: int) -> None:
    with pytest.raises(ValueError):
        to_roman(n)


@pytest.mark.parametrize("s", ["", "IIII", "VV", "IC", "ABC", "mcmxc", "IL", "1"])
def test_from_roman_invalid(s: str) -> None:
    with pytest.raises(ValueError):
        from_roman(s)


def test_round_trip() -> None:
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n
