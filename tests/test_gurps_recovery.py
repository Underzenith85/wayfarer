"""Hand-entered B424-427 numeric cases; profile certification stays separate."""

from typing import Final

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.fatigue import (
    ContinueExertion,
    FatigueCost,
    apply_fatigue,
    exertion_cost,
    fatigue_value,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.medical import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
    apply_recovery,
    first_aid_parameters,
    physician_parameters,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"


def seed(fp: int = 10, hp: int = 10) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(id="hp:a", current=hp, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=fp, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )


@pytest.mark.parametrize(
    "before,cost,after,hp_loss",
    [(10, 10, 0, 0), (1, 2, -1, 1), (0, 2, -2, 2), (-9, 3, -10, 3), (-10, 1, -10, 1)],
)
def test_signed_fatigue_costs(before: int, cost: int, after: int, hp_loss: int) -> None:
    state, result = apply_fatigue(
        seed(before),
        FatigueCost(id="cost", actor_id="a", expected_revision=0, amount=cost),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.pools[1].current == after
    assert state.pools[0].current == 10 - hp_loss
    assert result.hp_lost == hp_loss
    assert state.pools[1].fatigue is not None
    assert state.pools[1].fatigue.unconscious is (after == -10)
    assert apply_fatigue(
        state,
        FatigueCost(id="cost", actor_id="a", expected_revision=0, amount=cost),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    ) == (state, result)


def test_fatigue_movement_and_exertion_limits() -> None:
    assert fatigue_value(seed(4).pools[1], 7) == 7
    assert fatigue_value(seed(3).pools[1], 7) == 4
    assert exertion_cost("battle", seconds=10, encumbrance=4) == 0
    assert exertion_cost("battle", seconds=11, encumbrance=4, hot=True, heavy_clothing=True) == 7
    assert exertion_cost("hiking", seconds=7200, encumbrance=1) == 4
    assert exertion_cost("overexertion", seconds=3) == 3


def test_continued_exertion_and_critical_failure_are_saved() -> None:
    command = ContinueExertion(id="act", actor_id="a", expected_revision=0)
    state, result = apply_fatigue(
        seed(0), command, ht=10, will=10, rng=RecordedDice([6, 6, 6, 5, 5, 5]), system=True
    )
    assert not result.allowed
    assert state.pools[1].fatigue is not None and state.pools[1].fatigue.heart_attack
    assert apply_fatigue(state, command, ht=10, will=10, rng=RecordedDice([]), system=True) == (
        state,
        result,
    )
    with pytest.raises(ValidationError):
        apply_fatigue(seed(0), command, ht=10, rng=RecordedDice([]), system=True)


def test_rest_does_not_recover_restricted_fatigue_or_heal_hp() -> None:
    state, _ = apply_fatigue(
        seed(),
        FatigueCost(id="hunger", actor_id="a", expected_revision=0, amount=3, cause="starvation"),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    state, _ = apply_fatigue(
        state,
        FatigueCost(id="walk", actor_id="a", expected_revision=1, amount=2),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    context = CareContext(PROFILE, 10)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", expected_revision=2, kind="rest", target_id="a", seconds=1200
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    finish = FinishRecovery(id="finish", actor_id="a", expected_revision=3, task_id="rest")
    with pytest.raises(ValidationError, match="not due"):
        apply_recovery(state, finish, context, rng=RecordedDice([]), system=True)
    state = state.model_copy(update={"game_time": 1200})
    state, result = apply_recovery(state, finish, context, rng=RecordedDice([]), system=True)
    assert result.fp_recovered == 2 and state.pools[1].current == 7
    assert state.pools[0].current == 10
    assert apply_recovery(state, finish, context, rng=RecordedDice([]), system=True) == (
        state,
        result,
    )


@pytest.mark.parametrize(
    "tl,seconds,modifier",
    [
        (0, 1800, -4),
        (2, 1800, -3),
        (4, 1800, -2),
        (5, 1200, -2),
        (6, 1200, -1),
        (8, 600, 0),
        (9, 600, 1),
    ],
)
def test_first_aid_table(tl: int, seconds: int, modifier: int) -> None:
    assert first_aid_parameters(tl) == (seconds, modifier)


def test_bandage_and_first_aid_share_one_wound_budget() -> None:
    state, _ = apply_injury(
        seed(),
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=4,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    context = CareContext(PROFILE, 10, skill=12)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="bandage",
            actor_id="a",
            expected_revision=1,
            kind="bandage",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 60})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="bandage-end", actor_id="a", expected_revision=2, task_id="bandage"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.hp_recovered == 1
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="aid",
            actor_id="a",
            expected_revision=3,
            kind="first-aid",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 660})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="aid-end", actor_id="a", expected_revision=4, task_id="aid"),
        context,
        rng=RecordedDice([3, 3, 3, 3]),
        system=True,
    )
    assert result.hp_recovered == 2 and state.pools[0].current == 9
    assert any(e.id == "injury:wound" for e in state.events)
    with pytest.raises(ConflictError, match="already"):
        apply_recovery(
            state,
            BeginRecovery(
                id="aid-again",
                actor_id="a",
                expected_revision=5,
                kind="first-aid",
                target_id="a",
                wound_id="injury:wound",
            ),
            context,
            rng=RecordedDice([]),
            system=True,
        )


def test_natural_recovery_and_physician_schedule() -> None:
    assert physician_parameters(4) == (259200, 10)
    assert physician_parameters(9) == (43200, 50)
    context = CareContext(PROFILE, 10, food=True, physician_skill=12)
    state, _ = apply_recovery(
        seed(hp=-2),
        BeginRecovery(id="day", actor_id="a", expected_revision=0, kind="natural", target_id="a"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 86400})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="day-end", actor_id="a", expected_revision=1, task_id="day"),
        context,
        rng=RecordedDice([3, 4, 4]),
        system=True,
    )
    assert result.check is not None and result.check.effective_target == 11
    assert state.pools[0].current == -1


@pytest.mark.parametrize("cause", ["starvation", "dehydration", "sleep"])
def test_special_fatigue_requires_its_recovery_condition(cause: str) -> None:
    command = FatigueCost.model_validate(
        {"id": "special", "actor_id": "a", "expected_revision": 0, "amount": 3, "cause": cause}
    )
    state, _ = apply_fatigue(seed(), command, ht=10, rng=RecordedDice([]), system=True)
    context = CareContext(PROFILE, 10, food=True, water=True, sleep=True)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", expected_revision=1, kind="rest", target_id="a", seconds=86400
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 86400})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="rest"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_recovered == 3 and state.pools[1].current == 10


def test_new_wound_interrupts_treatment_without_consuming_first_aid_attempt() -> None:
    state, _ = apply_injury(
        seed(),
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=2,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    context = CareContext(PROFILE, 10, skill=12)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="aid",
            actor_id="a",
            expected_revision=1,
            kind="first-aid",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 30})
    state, _ = apply_injury(
        state,
        Wound(
            id="new-wound",
            actor_id="a",
            expected_revision=2,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].status == "interrupted"
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=3, task_id="aid"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "interrupted" and result.hp_recovered == 0
    _, result = apply_recovery(
        state,
        BeginRecovery(
            id="aid-again",
            actor_id="a",
            expected_revision=4,
            kind="first-aid",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "pending"


def test_failed_first_aid_cannot_be_rerolled_by_another_medic() -> None:
    state, _ = apply_injury(
        seed(),
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=2,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    context = CareContext(PROFILE, 10, skill=10)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="aid",
            actor_id="medic",
            expected_revision=1,
            kind="first-aid",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 600})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="medic", expected_revision=2, task_id="aid"),
        context,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert result.hp_recovered == 0
    with pytest.raises(ConflictError):
        apply_recovery(
            state,
            BeginRecovery(
                id="second-medic",
                actor_id="medic-2",
                expected_revision=3,
                kind="first-aid",
                target_id="a",
                wound_id="injury:wound",
            ),
            context,
            rng=RecordedDice([]),
            system=True,
        )


def test_critical_treatment_failure_is_injury_not_healing_or_retry() -> None:
    state, _ = apply_injury(
        seed(),
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=2,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    context = CareContext(PROFILE, 10, skill=10)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="aid",
            actor_id="medic",
            expected_revision=1,
            kind="first-aid",
            target_id="a",
            wound_id="injury:wound",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 600})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="medic", expected_revision=2, task_id="aid"),
        context,
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert result.hp_recovered == -2 and state.pools[0].current == 6
    assert len([e for e in state.events if e.id.startswith("injury:")]) == 2


def test_fp_floor_and_profile_validation() -> None:
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError):
        Pool(id="fp:a", current=-11, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE))
    with pytest.raises(SchemaError):
        Pool(id="hp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE))
    _, result = apply_fatigue(
        seed(-10),
        ContinueExertion(id="act", actor_id="a", expected_revision=0),
        ht=10,
        will=20,
        rng=RecordedDice([]),
        system=True,
    )
    assert not result.allowed
