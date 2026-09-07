"""Independent B423-424/B444 trauma and surgical-recovery cases for #209."""

from typing import Final

import pytest

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.location_types import LastingInjury
from wayfarer.rules.recovery_types import FatigueStatus, ProfileId, interrupt_tasks
from wayfarer.simulation.hazards import HazardCommand, apply_hazard
from wayfarer.simulation.recovery_variants import (
    BeginRecoveryVariant,
    FinishRecoveryVariant,
    RecoveryVariantContext,
    apply_recovery_variant,
    surgery_equipment_modifier,
)
from wayfarer.simulation.resources import Pool, ResourceState

PROFILE: Final[ProfileId] = "gurps-basic-set-4e-2004"
DAY = 86400


def mortal_state(*, due: int = 1800) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=-10,
                maximum=10,
                injury=InjuryStatus(
                    profile_id=PROFILE,
                    mortal_wound=True,
                    mortal_wound_due=due,
                    unconscious=True,
                ),
            ),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )


def lasting_state(*, permanent: bool = False) -> ResourceState:
    injury = LastingInjury(
        id="leg",
        location="left-leg",
        kind="crippled",
        duration="permanent" if permanent else "lasting",
        inflicted_at=0,
        injury=6,
        recovery_at=None if permanent else 30 * DAY,
    )
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=10,
                maximum=10,
                injury=InjuryStatus(
                    profile_id=PROFILE,
                    anatomy="human",
                    lasting_injuries=(injury,),
                ),
            ),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )


def test_trauma_maintenance_uses_higher_target_and_hourly_cadence() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, physician_skill=12, technology_level=6)
    state, pending = apply_recovery_variant(
        mortal_state(),
        BeginRecoveryVariant(
            id="maintain",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="trauma-maintenance",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert pending.status == "pending"
    assert state.recovery_tasks[0].kind == "physician"
    assert state.recovery_tasks[0].due == 3600
    hp = state.pools[0]
    assert hp.injury is not None and hp.injury.mortal_wound_due == 3600

    state = state.model_copy(update={"game_time": 3600})
    finish = FinishRecoveryVariant(
        id="maintain-finish",
        actor_id="b",
        expected_revision=1,
        task_id="maintain",
    )
    state, result = apply_recovery_variant(
        state, finish, context, rng=RecordedDice([4, 4, 4]), system=True
    )
    assert result.check is not None and result.check.effective_target == 12
    hp = state.pools[0]
    assert hp.injury is not None and hp.injury.mortal_wound_due == 7200
    assert hp.injury.mortal_wound and not hp.injury.dead

    reloaded = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_recovery_variant(reloaded, finish, context, rng=RecordedDice([]), system=True) == (
        reloaded,
        result,
    )


def test_life_support_changes_maintenance_to_daily_checks() -> None:
    context = RecoveryVariantContext(
        PROFILE, ht=12, physician_skill=10, technology_level=6, life_support=True
    )
    state, _ = apply_recovery_variant(
        mortal_state(),
        BeginRecoveryVariant(
            id="life-support",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="trauma-maintenance",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].due == DAY
    assert state.pools[0].injury is not None
    assert state.pools[0].injury.mortal_wound_due == DAY


def test_interrupted_maintenance_restores_half_hour_survival_deadline() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, physician_skill=12, technology_level=6)
    state, _ = apply_recovery_variant(
        mortal_state(),
        BeginRecoveryVariant(
            id="maintain",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="trauma-maintenance",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(
        update={
            "game_time": 600,
            "recovery_tasks": interrupt_tasks(state.recovery_tasks, frozenset({"b"}), 600),
        }
    )
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(
            id="settle-interruption",
            actor_id="b",
            expected_revision=1,
            task_id="maintain",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "interrupted" and result.check is None
    hp = state.pools[0]
    assert hp.injury is not None and hp.injury.mortal_wound_due == 2400


def test_maintenance_failure_kills_and_critical_success_pulls_through() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, physician_skill=10, technology_level=6)
    for dice, dead, stabilized in [([6, 6, 6], True, False), ([1, 1, 1], False, True)]:
        state, _ = apply_recovery_variant(
            mortal_state(),
            BeginRecoveryVariant(
                id="maintain",
                actor_id="b",
                expected_revision=0,
                target_id="a",
                kind="trauma-maintenance",
            ),
            context,
            rng=RecordedDice([]),
            system=True,
        )
        state = state.model_copy(update={"game_time": 3600})
        state, result = apply_recovery_variant(
            state,
            FinishRecoveryVariant(
                id="finish", actor_id="b", expected_revision=1, task_id="maintain"
            ),
            context,
            rng=RecordedDice(dice),
            system=True,
        )
        hp = state.pools[0]
        assert hp.injury is not None and hp.injury.dead is dead
        assert result.stabilized is stabilized
        assert hp.injury.mortal_wound is (not stabilized)


@pytest.mark.parametrize(
    "tl,expected",
    [(1, -6), (2, -5), (3, -5), (4, -4), (5, -2), (6, 0), (8, 2), (12, 6)],
)
def test_surgery_basic_equipment_modifiers(tl: int, expected: int) -> None:
    assert surgery_equipment_modifier(tl) == expected


def test_lasting_repair_shortens_remaining_months_to_weeks() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, surgery_skill=12, technology_level=6)
    state, _ = apply_recovery_variant(
        lasting_state(),
        BeginRecoveryVariant(
            id="repair",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="repair-lasting",
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].kind == "stabilize"
    assert state.recovery_tasks[0].due == 7200
    state = state.model_copy(update={"game_time": 7200})
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(
            id="repair-finish", actor_id="b", expected_revision=1, task_id="repair"
        ),
        context,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.repaired
    hp = state.pools[0]
    assert hp.injury is not None
    injury = hp.injury.lasting_injuries[0]
    remaining = 30 * DAY - 7200
    assert injury.recovery_at == 7200 + (remaining * 7 + 29) // 30


def test_critical_surgery_failure_makes_injury_permanent_and_inflicts_3d() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, surgery_skill=12, technology_level=6)
    state, _ = apply_recovery_variant(
        lasting_state(),
        BeginRecoveryVariant(
            id="repair",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="repair-lasting",
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 7200})
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(
            id="repair-finish", actor_id="b", expected_revision=1, task_id="repair"
        ),
        context,
        rng=RecordedDice([6, 6, 6, 1, 1, 1]),
        system=True,
    )
    assert result.permanent and result.hp_lost == 3
    assert state.pools[0].current == 7
    assert state.pools[0].injury is not None
    injury = state.pools[0].injury.lasting_injuries[0]
    assert injury.duration == "permanent" and injury.recovery_at is None


def test_low_tl_surgery_can_schedule_infection_in_existing_hazard_engine() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, surgery_skill=15, technology_level=4)
    state, _ = apply_recovery_variant(
        lasting_state(),
        BeginRecoveryVariant(
            id="repair",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="repair-lasting",
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].treatment_modifier == -4
    state = state.model_copy(update={"game_time": 7200})
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(
            id="repair-finish", actor_id="b", expected_revision=1, task_id="repair"
        ),
        context,
        rng=RecordedDice([3, 3, 3, 5, 5, 5]),
        system=True,
    )
    assert result.repaired and result.infection_schedule_id == "infection:repair"
    assert result.infection_check is not None and not result.infection_check.outcome.succeeded
    schedule = state.hazards[0]
    assert schedule.spec.kind == "disease" and schedule.due == 7200 + DAY

    state = state.model_copy(update={"game_time": schedule.due})
    before = state.pools[0].current
    state, hazard = apply_hazard(
        state,
        HazardCommand(
            id="infection-day",
            actor_id="a",
            expected_revision=state.revision,
            kind="resolve",
            hazard_id=schedule.id,
        ),
        schedule,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert hazard.hp_lost == 1 and state.pools[0].current == before - 1
    assert state.illnesses and state.illnesses[0].blocks_natural_healing


def test_unclean_surgery_penalty_and_tl5_optional_infection_are_persisted() -> None:
    context = RecoveryVariantContext(
        PROFILE,
        ht=10,
        surgery_skill=15,
        technology_level=5,
        sterile=False,
        anesthetic=False,
        infection_risk=True,
        infection_modifier=-2,
    )
    state, _ = apply_recovery_variant(
        lasting_state(),
        BeginRecoveryVariant(
            id="repair",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="repair-lasting",
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].treatment_modifier == -7
    state = state.model_copy(update={"game_time": 7200})
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(id="finish", actor_id="b", expected_revision=1, task_id="repair"),
        context,
        rng=RecordedDice([2, 2, 2, 5, 5, 5]),
        system=True,
    )
    assert result.repaired and result.infection_schedule_id == "infection:repair"
    assert state.hazards[0].spec.resistance_modifier == -2


def test_permanent_repair_is_explicitly_fail_closed() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, surgery_skill=16, technology_level=8)
    command = BeginRecoveryVariant(
        id="radical",
        actor_id="b",
        expected_revision=0,
        target_id="a",
        kind="repair-permanent",
        injury_id="leg",
    )
    with pytest.raises(ValidationError, match="setting-defined"):
        apply_recovery_variant(
            lasting_state(permanent=True), command, context, rng=RecordedDice([]), system=True
        )
    with pytest.raises(ValidationError, match="authoritative"):
        apply_recovery_variant(
            lasting_state(permanent=True), command, context, rng=RecordedDice([])
        )


def test_interrupted_surgery_never_rolls_or_changes_the_lasting_injury() -> None:
    context = RecoveryVariantContext(PROFILE, ht=10, surgery_skill=12, technology_level=6)
    original = lasting_state()
    assert original.pools[0].injury is not None
    before_injuries = original.pools[0].injury.lasting_injuries
    state, _ = apply_recovery_variant(
        original,
        BeginRecoveryVariant(
            id="repair",
            actor_id="b",
            expected_revision=0,
            target_id="a",
            kind="repair-lasting",
            injury_id="leg",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(
        update={
            "game_time": 1000,
            "recovery_tasks": interrupt_tasks(state.recovery_tasks, frozenset({"b"}), 1000),
        }
    )
    state, result = apply_recovery_variant(
        state,
        FinishRecoveryVariant(
            id="interrupted", actor_id="b", expected_revision=1, task_id="repair"
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "interrupted" and result.check is None
    assert state.pools[0].injury is not None
    assert state.pools[0].injury.lasting_injuries == before_injuries
    with pytest.raises(ConflictError):
        apply_recovery_variant(
            state,
            FinishRecoveryVariant(id="again", actor_id="b", expected_revision=2, task_id="repair"),
            context,
            rng=RecordedDice([]),
            system=True,
        )
