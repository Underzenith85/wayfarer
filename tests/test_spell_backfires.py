"""Hand-entered B235-237 numeric cases, Characters 4e third printing (2008)."""

from typing import Literal

import pytest
from test_injury import state as injured_state
from test_injury import wound
from test_spells import state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.health.injury import InjuryTurn, apply_injury
from wayfarer.engine.simulation.magic.backfires import (
    WEEK,
    apply_backfire,
    backfires,
    forgotten,
    refund_due,
    refund_later,
    remember,
    require_settled,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


def backfire(
    dice: list[int], *, severity: Literal["normal", "mild", "disaster"] = "normal"
) -> ResourceState:
    return apply_backfire(
        state(),
        command_id="critical",
        actor_id="a",
        cast_id="cast",
        spell_id="light",
        ht=10,
        severity=severity,
        rng=RecordedDice(dice),
    )


@pytest.mark.parametrize("dice,lost", [([1, 1, 1, 4], 4), ([3, 3, 2], 1)])
def test_backfire_injury_is_real_and_survives_serialization(dice: list[int], lost: int) -> None:
    saved = ResourceState.model_validate_json(backfire(dice).model_dump_json())
    assert saved.pools[0].current == 10 - lost
    assert not backfires(saved)[0].pending


@pytest.mark.parametrize(
    "dice", [[1, 1, 2], [1, 2, 2], [2, 2, 2], [3, 2, 2], [5, 4, 4], [5, 5, 5], [6, 5, 5], [6, 6, 6]]
)
def test_contextual_backfire_is_durable_and_blocks_actions(dice: list[int]) -> None:
    saved = ResourceState.model_validate_json(backfire(dice).model_dump_json())
    with pytest.raises(ConflictError, match="backfire"):
        require_settled(saved, "a")
    require_settled(saved, "b")


def test_mental_stun_uses_iq_not_ht() -> None:
    saved = backfire([3, 3, 3])
    saved, _ = apply_injury(
        saved,
        InjuryTurn(
            id="start",
            actor_id="a",
            expected_revision=saved.revision,
            turn=1,
            phase="start",
            do_nothing=True,
        ),
        ht=8,
        stun_iq=14,
        rng=RecordedDice([]),
        system=True,
    )
    saved, result = apply_injury(
        saved,
        InjuryTurn(
            id="end",
            actor_id="a",
            expected_revision=saved.revision,
            turn=1,
            phase="end",
            do_nothing=True,
        ),
        ht=8,
        stun_iq=14,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert result.checks[0].check.effective_target == 14
    assert saved.pools[0].injury and not saved.pools[0].injury.stunned


def test_forgetting_waits_a_week_and_failed_check_cannot_be_banked() -> None:
    saved = backfire([6, 6, 5])
    assert forgotten(saved, "a", "light")
    with pytest.raises(ConflictError):
        remember(saved, "a", "cast", "early", 10, RecordedDice([]))
    saved, check = remember(
        saved.model_copy(update={"game_time": WEEK * 3}),
        "a",
        "cast",
        "first",
        10,
        RecordedDice([5, 5, 5]),
    )
    assert not check.outcome.succeeded
    assert backfires(saved)[0].remember_at == WEEK * 4
    saved, check = remember(
        saved.model_copy(update={"game_time": WEEK * 4}),
        "a",
        "cast",
        "second",
        10,
        RecordedDice([3, 3, 3]),
    )
    assert check.outcome.succeeded and not forgotten(saved, "a", "light")


def test_mana_severities_do_not_roll_a_normal_table() -> None:
    assert not backfires(backfire([], severity="mild"))[0].pending
    assert backfires(backfire([], severity="disaster"))[0].pending


@pytest.mark.parametrize("hp,damage,dice", [(-9, 2, [6, 6, 6]), (-49, 1, [])])
def test_burning_hp_never_spends_a_fatal_payment(hp: int, damage: int, dice: list[int]) -> None:
    command = wound(damage)
    saved, result = apply_injury(
        injured_state(hp), command, ht=10, rng=RecordedDice(dice), system=True, burning_hp=True
    )
    assert result.injury == 0
    assert saved.pools[0].current == hp
    assert (
        saved.pools[0].injury
        and saved.pools[0].injury.unconscious
        and not saved.pools[0].injury.dead
    )
    assert apply_injury(
        saved, command, ht=10, rng=RecordedDice([]), system=True, burning_hp=True
    ) == (saved, result)


def test_combat_refund_uses_caster_turn_and_does_not_create_credit() -> None:
    saved = refund_later(state(), "a", "cost", 3, combat=True)
    assert refund_due(saved.model_copy(update={"game_time": 50}), "a").pools[1].current == 10
    saved = refund_due(saved, "a", turn=1)
    # No FP was missing when due. A later cost cannot spend a stored refund.
    pools = (saved.pools[0], saved.pools[1].model_copy(update={"current": 7}))
    saved = saved.model_copy(update={"pools": pools})
    assert refund_due(saved, "a", turn=2).pools[1].current == 7
