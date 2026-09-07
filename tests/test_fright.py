"""Hand-entered numeric table expectations, B360-361; no generated rule prose."""

import pytest

from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.fright import fright_effect


@pytest.mark.parametrize("total", range(4, 42))
def test_all_fright_rows_resolve(total: int) -> None:
    result = fright_effect(total, 10, rng=RecordedDice([3] * 30))
    assert result.table_total == total
    assert result.duration_seconds >= 0
    if total >= 40:
        assert result.permanent_iq_loss == 1 and result.trait_points == -15


@pytest.mark.parametrize(
    "total,dice,condition,seconds,recovery",
    [
        (4, [], "stunned", 1, "none"),
        (6, [], "stunned", 1, "will"),
        (8, [], "stunned", 1, "modified-will"),
        (10, [4], "stunned", 4, "modified-will"),
        (11, [2, 3], "stunned", 5, "modified-will"),
        (12, [], "retching", 15, "ht"),
        (17, [2], "unconscious", 120, "ht"),
        (19, [2, 3], "unconscious", 300, "ht"),
        (21, [3], "panic", 180, "will"),
        (28, [], "unconscious", 1800, "ht"),
        (29, [2], "unconscious", 7200, "ht"),
        (30, [2], "catatonia", 172800, "ht"),
    ],
)
def test_durations_and_recovery_targets(
    total: int, dice: list[int], condition: str, seconds: int, recovery: str
) -> None:
    rng = RecordedDice(dice)
    result = fright_effect(total, 10, rng=rng)
    assert result.condition == condition and result.duration_seconds == seconds
    assert result.recovery_attribute == recovery and rng.exhausted()


def test_physical_losses_and_gm_choices() -> None:
    result = fright_effect(31, 10, rng=RecordedDice([2, 4, 6, 6, 6, 3]))
    assert result.duration_seconds == 120 and result.fp_loss == 4 and result.hp_loss == 3
    assert result.permanent_ht_loss == 1
    assert fright_effect(32, 10, rng=RecordedDice([4, 5])).hp_loss == 9
    assert fright_effect(13, 10, rng=RecordedDice([])).trait_choice == "quirk"
    assert fright_effect(25, 10, rng=RecordedDice([])).trait_choice == "worsen-self-control"
    assert fright_effect(37, 10, rng=RecordedDice([])).trait_points == -30
