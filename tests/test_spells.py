"""Hand-entered Basic Set B235-241, B246-247, B249-250 lifecycle examples.

Provisional first-printing/2007-01-26 baseline; source verification pending.
"""

import pytest

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.recovery_types import FatigueStatus
from wayfarer.simulation.abilities import interrupt_concentration
from wayfarer.simulation.resources import Pool, ResourceState
from wayfarer.simulation.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    SpellId,
    active_spells,
    apply_spell,
    casting_seconds,
    cost_reduction,
    latest,
)


def state() -> ResourceState:
    return ResourceState(
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )


def context() -> SpellContext:
    return SpellContext(
        profile_id=PROFILE,
        build_revision="approved",
        skill=14,
        magery=1,
        target_id="b",
        learned=(
            "light",
            "daze",
            "foolishness",
            "fireball",
            "create-fire",
            "shape-fire",
            "ignite-fire",
        ),
    )


def command(revision: int = 0, *, kind: str = "start", spell: SpellId = "light") -> SpellCommand:
    return SpellCommand.model_validate(
        dict(
            id=f"c{revision}",
            actor_id="a",
            expected_revision=revision,
            kind=kind,
            spell_id=spell,
            cast_id="cast",
        )
    )


@pytest.mark.parametrize(
    "skill,cost,seconds", [(14, 0, 8), (15, 1, 8), (19, 1, 8), (20, 2, 4), (25, 3, 2), (30, 4, 1)]
)
def test_skill_benefit_boundaries(skill: int, cost: int, seconds: int) -> None:
    assert cost_reduction(skill) == cost
    assert casting_seconds(8, skill) == seconds
    assert casting_seconds(1, skill, missile=True) == 1


def test_success_retry_restart_maintain_cancel() -> None:
    started, result = apply_spell(state(), command(), context(), rng=RecordedDice([]), system=True)
    assert result.outcome == "casting"
    assert started.pools[1].current == 10
    completed = command(1, kind="complete")
    cast, result = apply_spell(
        started.model_copy(update={"game_time": 1}),
        completed,
        context(),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.outcome == "active" and result.energy_spent == 1
    assert cast.pools[1].current == 9
    assert active_spells(cast)[0].expires_at == 61
    restored = ResourceState.model_validate_json(cast.model_dump_json())
    replay, repeated = apply_spell(
        restored, completed, context(), rng=RecordedDice([]), system=True
    )
    assert replay == restored and repeated == result
    assert len(replay.events) == len(cast.events)
    due = restored.model_copy(update={"game_time": 61})
    assert not active_spells(due)
    maintained, result = apply_spell(
        due, command(2, kind="maintain"), context(), rng=RecordedDice([]), system=True
    )
    assert result.energy_spent == 1 and maintained.pools[1].current == 8
    assert active_spells(maintained)[0].expires_at == 121
    ended, _ = apply_spell(
        maintained, command(3, kind="cancel"), context(), rng=RecordedDice([]), system=True
    )
    assert not active_spells(ended) and ended.pools[1].current == 7
    with pytest.raises(ConflictError):
        apply_spell(ended, command(4), context(), rng=RecordedDice([]), system=True)


@pytest.mark.parametrize(
    "dice,outcome,cost",
    [
        ([6, 5, 5], "failed", 1),
        ([6, 6, 6], "critical-failure", 3),
        ([1, 1, 1], "active", 0),
        ([3, 3, 3, 1, 1, 1], "resisted", 3),
        ([3, 3, 3, 4, 4, 4], "active", 3),
    ],
)
def test_resisted_spell_costs(dice: list[int], outcome: str, cost: int) -> None:
    started, _ = apply_spell(
        state(), command(spell="daze"), context(), rng=RecordedDice([]), system=True
    )
    rng = RecordedDice(dice)
    cast, result = apply_spell(
        started.model_copy(update={"game_time": 2}),
        command(1, kind="complete", spell="daze"),
        context(),
        rng=rng,
        system=True,
    )
    assert rng.exhausted()
    assert result.outcome == outcome and result.energy_spent == cost
    assert cast.pools[1].current == 10 - cost


def test_rule16_tie_resists_without_second_cast_roll() -> None:
    ctx = context().model_copy(update={"skill": 30, "target_ht": 16})
    started, _ = apply_spell(state(), command(spell="daze"), ctx, rng=RecordedDice([]), system=True)
    _, result = apply_spell(
        started.model_copy(update={"game_time": 1}),
        command(1, kind="complete", spell="daze"),
        ctx,
        rng=RecordedDice([3, 3, 3, 3, 3, 3]),
        system=True,
    )
    assert result.outcome == "resisted"
    assert len(result.checks) == 2


@pytest.mark.parametrize(
    "mana,magery,learned",
    [
        ("none", 1, ("light",)),
        ("very-high", 1, ("light",)),
        ("normal", -1, ("light",)),
        ("normal", 1, ()),
    ],
)
def test_fail_closed_before_dice(mana: str, magery: int, learned: tuple[str, ...]) -> None:
    ctx = SpellContext.model_validate(
        {**context().model_dump(), "mana": mana, "magery": magery, "learned": learned}
    )
    with pytest.raises(ValidationError):
        apply_spell(state(), command(), ctx, rng=RecordedDice([]), system=True)


def test_low_mana_penalty_and_high_mana_nonmage() -> None:
    started, _ = apply_spell(
        state(),
        command(),
        context().model_copy(update={"mana": "low", "skill": 20}),
        rng=RecordedDice([]),
        system=True,
    )
    assert latest(started)["cast"].skill == 15
    assert latest(started)["cast"].cost == 0
    apply_spell(
        state(),
        command(),
        context().model_copy(update={"mana": "high", "magery": -1}),
        rng=RecordedDice([]),
        system=True,
    )


def test_distraction_and_other_action_interrupt_across_reload() -> None:
    started, _ = apply_spell(state(), command(), context(), rng=RecordedDice([]), system=True)
    distracted = interrupt_concentration(started, "a", "defend", distraction=True)
    restored = ResourceState.model_validate_json(distracted.model_dump_json()).model_copy(
        update={"game_time": 1}
    )
    cast, result = apply_spell(
        restored, command(1, kind="complete"), context(), rng=RecordedDice([4, 4, 4]), system=True
    )
    assert result.outcome == "interrupted" and cast.pools[1].current == 10
    abandoned = interrupt_concentration(started, "a", "attack")
    with pytest.raises(ConflictError):
        apply_spell(
            abandoned.model_copy(update={"game_time": 1}),
            command(1, kind="complete"),
            context(),
            rng=RecordedDice([]),
            system=True,
        )


@pytest.mark.parametrize("at", [0, 2, 100])
def test_cannot_bank_elapsed_concentration(at: int) -> None:
    started, _ = apply_spell(state(), command(), context(), rng=RecordedDice([]), system=True)
    with pytest.raises(ConflictError):
        apply_spell(
            started.model_copy(update={"game_time": at}),
            command(1, kind="complete"),
            context(),
            rng=RecordedDice([]),
            system=True,
        )


def test_area_cost_and_missile_energy_binding() -> None:
    ctx = context().model_copy(update={"radius": 3})
    started, _ = apply_spell(
        state(), command(spell="create-fire"), ctx, rng=RecordedDice([]), system=True
    )
    assert latest(started)["cast"].cost == 6
    assert latest(started)["cast"].maintenance == 3
    assert latest(started)["cast"].radius == 3
    ctx = context().model_copy(update={"energy": 2, "magery": 2})
    started, _ = apply_spell(
        state(), command(spell="fireball"), ctx, rng=RecordedDice([]), system=True
    )
    cast, result = apply_spell(
        started.model_copy(update={"game_time": 1}),
        command(1, kind="complete", spell="fireball"),
        ctx,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.energy_spent == 2 and active_spells(cast)[0].energy == 2
    assert active_spells(cast)[0].expires_at is None


def test_wrong_authority_profile_stale_and_command_reuse() -> None:
    with pytest.raises(ValidationError):
        apply_spell(state(), command(), context(), rng=RecordedDice([]))
    with pytest.raises(ValidationError):
        apply_spell(
            state(),
            command(),
            context().model_copy(update={"profile_id": "gurps-lite-4e-2004"}),
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ConflictError):
        apply_spell(state(), command(1), context(), rng=RecordedDice([]), system=True)
    started, _ = apply_spell(state(), command(), context(), rng=RecordedDice([]), system=True)
    with pytest.raises(ConflictError):
        apply_spell(
            started,
            command().model_copy(update={"cast_id": "another"}),
            context(),
            rng=RecordedDice([]),
            system=True,
        )


def test_early_late_and_duplicate_maintenance_rejected() -> None:
    started, _ = apply_spell(state(), command(), context(), rng=RecordedDice([]), system=True)
    cast, _ = apply_spell(
        started.model_copy(update={"game_time": 1}),
        command(1, kind="complete"),
        context(),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    for at in (60, 62):
        with pytest.raises(ConflictError):
            apply_spell(
                cast.model_copy(update={"game_time": at}),
                command(2, kind="maintain"),
                context(),
                rng=RecordedDice([]),
                system=True,
            )
    maintained, _ = apply_spell(
        cast.model_copy(update={"game_time": 61}),
        command(2, kind="maintain"),
        context(),
        rng=RecordedDice([]),
        system=True,
    )
    with pytest.raises(ConflictError):
        apply_spell(
            maintained, command(3, kind="maintain"), context(), rng=RecordedDice([]), system=True
        )


def test_cancellation_cost_is_not_reduced_by_skill_and_expiry_is_free() -> None:
    ctx = context().model_copy(update={"skill": 20})
    started, _ = apply_spell(state(), command(), ctx, rng=RecordedDice([]), system=True)
    cast, _ = apply_spell(
        started.model_copy(update={"game_time": 1}),
        command(1, kind="complete"),
        ctx,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    ended, result = apply_spell(
        cast, command(2, kind="cancel"), ctx, rng=RecordedDice([]), system=True
    )
    assert result.energy_spent == 1 and ended.pools[1].current == 9
    _, expired = apply_spell(
        cast.model_copy(update={"game_time": 61}),
        command(2, kind="cancel"),
        ctx,
        rng=RecordedDice([]),
        system=True,
    )
    assert expired.energy_spent == 0
    _, aborted = apply_spell(
        started, command(1, kind="cancel"), ctx, rng=RecordedDice([]), system=True
    )
    assert aborted.energy_spent == 0


def test_depleted_casting_energy_rejects_before_completion_dice() -> None:
    started, _ = apply_spell(
        state(), command(spell="daze"), context(), rng=RecordedDice([]), system=True
    )
    depleted = started.model_copy(
        update={
            "game_time": 2,
            "pools": (started.pools[0], started.pools[1].model_copy(update={"current": 1})),
        }
    )
    with pytest.raises(ConflictError, match="casting energy"):
        apply_spell(
            depleted,
            command(1, kind="complete", spell="daze"),
            context(),
            rng=RecordedDice([]),
            system=True,
        )
