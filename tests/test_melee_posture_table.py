"""Selected Campaigns B551: melee posture modifiers and the Reach C footnote."""

import pytest

from wayfarer.engine.rules.tables.combat import (
    Posture,
    posture_melee_modifier,
    posture_melee_reach_allowed,
)


@pytest.mark.parametrize(
    "posture,modifier",
    [
        ("standing", 0),
        ("crouching", -2),
        ("kneeling", -2),
        ("crawling", -4),
        ("sitting", -2),
        ("prone", -4),
    ],
)
def test_source_melee_posture_table_and_historical_policy(posture: Posture, modifier: int) -> None:
    assert posture_melee_modifier(posture) == modifier
    assert posture_melee_modifier(posture, legacy=True) == (
        -4 if posture == "prone" else -2 if posture == "kneeling" else 0
    )
    assert posture_melee_reach_allowed(posture, close=True)
    assert posture_melee_reach_allowed(posture, close=False) == (posture != "crawling")
    assert posture_melee_reach_allowed(posture, close=False, legacy=True)
