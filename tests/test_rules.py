from hypothesis import given
from hypothesis import strategies as st

from wayfarer import validation
from wayfarer.character import builder
from wayfarer.rules import checks


def test_default_character_is_legal() -> None:
    assert builder.validate(builder.character())["valid"]


@given(st.one_of(st.integers(max_value=7), st.integers(min_value=15), st.booleans()))
def test_attribute_bounds_reject_invalid_values(value: int | bool) -> None:
    draft = builder.character()
    draft["attributes"]["ST"] = value
    assert not builder.validate(draft)["valid"]


def test_character_schema_rejects_unknown_and_coerced_fields() -> None:
    original = validation.mapping(builder.character())
    original["free_points"] = 999
    assert not builder.validate(original)["valid"]
    draft = builder.character()
    draft["attributes"]["IQ"] = True
    assert not builder.validate(draft)["valid"]


def test_character_schema_returns_independent_containers() -> None:
    original = builder.character()
    parsed = validation.character(original)
    parsed["attributes"]["ST"] = 99
    assert original["attributes"]["ST"] == 10


def test_roll_uses_injected_randomness() -> None:
    class Lowest:
        def randbelow(self, exclusive_upper_bound: int, /) -> int:
            assert exclusive_upper_bound == 6
            return 0

    assert checks.roll(2, Lowest())["critical"] == "success"
