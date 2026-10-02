"""Exact B346 outcomes and real prerequisites at each private pending-roll seam."""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError as ModelValidationError
from test_campaign_activities import ACTOR
from test_luck import SEED, approved

from wayfarer.engine.rules.checks import Modifier, Outcome, RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.campaign._task_phases import (
    DAY,
    REST_PREFIX,
    LongTaskPreparation,
    LongTaskWorkRestriction,
    ensure_long_task_available,
    prepare_long_task,
    roll_long_task_check,
    score_long_task_check,
    select_long_task_check,
)
from wayfarer.engine.simulation.campaign.activities import (
    ActivityActor,
    ActivityOutcome,
    LongTaskRule,
    PerformActivity,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, LuckState, apply_luck
from wayfarer.errors import ConflictError, ValidationError

RULE = LongTaskRule(id="boat", target_id="skill:carpentry", required_man_hours=16)
SUPERVISOR = ACTOR.model_copy(
    update={"actor_id": "supervisor", "targets": (("skill:administration", 12),)}
)


def resources(*, fp: int = 10) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="fp:a",
                current=fp,
                maximum=10,
                fatigue=FatigueStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
            Pool(
                id="hp:a",
                current=10,
                maximum=10,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human"),
            ),
        )
    )


def begin(
    state: ResourceState | None = None,
    *,
    identity: str = "shift",
    hours: int = 8,
    started_at: int = 0,
    actor: ActivityActor = ACTOR,
    supervisor: ActivityActor | None = None,
    supervisor_target_id: Literal["skill:administration", "skill:leadership"] | None = None,
) -> tuple[ResourceState, LongTaskPreparation]:
    state = resources() if state is None else state
    command = PerformActivity(
        id=identity,
        actor_id=actor.actor_id,
        expected_revision=state.revision,
        activity_id=RULE.id,
        seconds=hours * 3600,
    )
    state = state.model_copy(
        update={"game_time": started_at + command.seconds, "revision": state.revision + 1}
    )
    return state, prepare_long_task(
        state,
        command,
        RULE,
        actor,
        started_at=started_at,
        supervisor=supervisor,
        supervisor_target_id=supervisor_target_id,
    )


def select(
    state: ResourceState,
    preparation: LongTaskPreparation,
    dice: tuple[int, int, int],
    *,
    follow_up: tuple[int, ...] = (),
) -> tuple[ResourceState, LongTaskPreparation, ActivityOutcome | None]:
    check = score_long_task_check(preparation, dice)
    rng = RecordedDice(follow_up)
    result = select_long_task_check(state, preparation, check, rng=rng, system=True)
    assert rng.exhausted()
    return result


@pytest.mark.parametrize("dice,progress", [((3, 3, 3), 8), ((1, 1, 1), 12), ((5, 4, 4), 4)])
def test_worker_exact_eight_hour_contributions(dice: tuple[int, int, int], progress: int) -> None:
    state, preparation = begin()
    assert preparation.phase == "worker" and preparation.check_actor_id == "a"
    original_rng = RecordedDice(dice)
    original = roll_long_task_check(preparation, rng=original_rng)
    assert original_rng.exhausted()
    assert not state.events and state.game_time == 28800
    after, complete, outcome = select_long_task_check(
        state, preparation, original, rng=RecordedDice(()), system=True
    )
    assert outcome is not None
    assert (outcome.progress, outcome.total_progress, outcome.ruined_hours) == (
        Decimal(progress),
        Decimal(progress),
        0,
    )
    assert complete.phase == "complete" and after.game_time == state.game_time
    assert after.pools == state.pools


def test_only_selected_critical_failure_rolls_followup_and_prior_damage_stays_destroyed() -> None:
    state, preparation = begin(identity="day-1")
    state, _, result = select(state, preparation, (3, 3, 3))
    assert result is not None and result.total_progress == 8
    state, preparation = begin(state, identity="day-2", started_at=DAY)
    original = roll_long_task_check(preparation, rng=RecordedDice((3, 3, 3)))
    assert original.outcome is Outcome.SUCCESS
    state, _, result = select(state, preparation, (6, 6, 6), follow_up=(2, 4))
    assert result is not None and (result.progress, result.ruined_hours, result.total_progress) == (
        Decimal(0),
        6,
        Decimal(2),
    )
    state, preparation = begin(state, identity="day-3", started_at=2 * DAY)
    original = roll_long_task_check(preparation, rng=RecordedDice((6, 6, 6)))
    assert original.outcome is Outcome.CRITICAL_FAILURE
    state, _, result = select(state, preparation, (3, 3, 3))
    assert result is not None and (result.progress, result.ruined_hours, result.total_progress) == (
        Decimal(8),
        0,
        Decimal(10),
    )
    assert not result.completed


@pytest.mark.parametrize(
    "hours,ht_dice,ht_target,worker_target,fp_lost",
    [(10, (5, 5, 5), 12, 9, 3), (10, (5, 4, 4), 12, 10, 2), (11, (4, 4, 4), 11, 10, 2)],
)
def test_overtime_penalty_and_real_fp_commit_before_worker(
    hours: int, ht_dice: tuple[int, int, int], ht_target: int, worker_target: int, fp_lost: int
) -> None:
    state, preparation = begin(hours=hours)
    assert preparation.phase == "overtime" and preparation.check_target == ht_target
    ht_id = preparation.check_id
    state, worker, outcome = select(state, preparation, ht_dice)
    assert outcome is None and worker.phase == "worker"
    assert worker.check_id != ht_id and worker.check_actor_id == "a"
    assert worker.check_target == worker_target
    assert next(pool.current for pool in state.pools if pool.id == "fp:a") == 10 - fp_lost
    assert worker.fatigue is not None and worker.fatigue.fp_lost == fp_lost
    selected_state, _, outcome = select(state, worker, (3, 3, 3))
    assert outcome is not None
    assert (
        outcome.progress,
        outcome.fp_lost,
        outcome.checks[0].total,
        outcome.checks[1].effective_target,
    ) == (Decimal(hours), fp_lost, sum(ht_dice), worker_target)
    assert selected_state.pools == state.pools


def test_overtime_success_has_no_cost_or_penalty() -> None:
    state, preparation = begin(hours=10)
    state, worker, _ = select(state, preparation, (4, 4, 4))
    assert worker.phase == "worker" and worker.check_target == 12 and worker.fatigue is None
    assert next(pool.current for pool in state.pools if pool.id == "fp:a") == 10
    _, _, outcome = select(state, worker, (1, 1, 1))
    assert outcome is not None and outcome.progress == 15 and outcome.fp_lost == 0


@pytest.mark.parametrize(
    "ht_dice,worker_dice,progress,fp_lost",
    [
        ((3, 3, 3), (5, 5, 5), Decimal(5), 0),
        ((5, 5, 5), (4, 4, 4), Decimal(4), 3),
        ((6, 6, 6), (2, 2, 3), Decimal(4), 6),
    ],
)
def test_overtime_failure_uses_ordinary_failure_baseline_unless_ht_succeeded(
    ht_dice: tuple[int, int, int],
    worker_dice: tuple[int, int, int],
    progress: Decimal,
    fp_lost: int,
) -> None:
    """Literal B346 branch reading: failed HT retains extra labor on skill success."""
    state, overtime = begin(hours=10)
    state, worker, _ = select(state, overtime, ht_dice)
    selected = score_long_task_check(worker, worker_dice)
    assert selected.outcome is Outcome.FAILURE
    state, complete, outcome = select_long_task_check(
        state,
        worker,
        selected,
        rng=RecordedDice(()),
        system=True,
    )
    assert complete.phase == "complete" and outcome is not None
    assert outcome.progress == progress and outcome.total_progress == progress
    assert outcome.fp_lost == fp_lost and outcome.elapsed_seconds == 36000
    assert state.game_time == 36000


def test_canonical_fatigue_secondary_injury_roll_finishes_before_worker_original() -> None:
    actor = ACTOR.model_copy(update={"ht": 10, "current_fp": 1})
    state, preparation = begin(resources(fp=1), hours=10, actor=actor)
    state, worker, outcome = select(state, preparation, (6, 6, 6), follow_up=(3, 3, 3))
    assert outcome is None and worker.phase == "worker" and worker.check_target == 4
    assert {pool.id: pool.current for pool in state.pools} == {"fp:a": -7, "hp:a": 3}
    assert worker.fatigue is not None and worker.fatigue.injury is not None
    assert worker.fatigue.injury.checks[0].check.total == 9
    state, _, outcome = select(state, worker, (1, 1, 1))
    assert outcome is not None
    assert (outcome.progress, outcome.fp_lost, outcome.hp_lost) == (Decimal(15), 8, 7)
    assert tuple(check.total for check in outcome.checks) == (18, 9, 3)


def test_next_day_rest_is_persisted_at_overtime_before_worker_and_crosses_midnight() -> None:
    start = 20 * 3600
    state, preparation = begin(hours=10, started_at=start)
    state, worker, outcome = select(state, preparation, (6, 6, 6))
    assert outcome is None and worker.phase == "worker"
    event = next(event for event in state.events if event.id.startswith(REST_PREFIX))
    rest = LongTaskWorkRestriction.model_validate_json(event.kind)
    assert (rest.starts_at, rest.ends_at) == (start + DAY, start + 2 * DAY)
    assert rest.starts_at > state.game_time
    restored = ResourceState.model_validate_json(state.model_dump_json())
    for beginning in (rest.starts_at - 3600, rest.starts_at, rest.ends_at - 3600):
        with pytest.raises(ValidationError, match="exhausted next day"):
            ensure_long_task_available(restored, "a", started_at=beginning, seconds=7200)
    ensure_long_task_available(restored, "a", started_at=rest.ends_at, seconds=8 * 3600)
    ensure_long_task_available(
        restored, "other-worker", started_at=rest.starts_at, seconds=8 * 3600
    )
    _, _, result = select(restored, worker, (1, 1, 1))
    assert result is not None and result.consequence == "exhausted-next-day"


@pytest.mark.parametrize("dice,bonus", [((3, 3, 3), 1), ((1, 1, 1), 2), ((6, 6, 6), 0)])
def test_separate_supervisor_check_has_own_identity_and_gives_exact_bonus(
    dice: tuple[int, int, int], bonus: int
) -> None:
    state, preparation = begin(supervisor=SUPERVISOR, supervisor_target_id="skill:administration")
    assert preparation.phase == "supervision" and preparation.check_actor_id == "supervisor"
    supervisor_roll_id = preparation.check_id
    state, worker, outcome = select(state, preparation, dice)
    assert outcome is None and worker.phase == "worker" and worker.check_actor_id == "a"
    assert worker.check_id != supervisor_roll_id and worker.check_target == 12 + bonus
    assert state.pools == resources().pools
    _, _, outcome = select(state, worker, (5, 4, 4))
    assert outcome is not None and outcome.progress == (8 if bonus else 4)
    assert tuple(check.dice for check in outcome.checks) == (dice, (5, 4, 4))


def test_supervisor_cannot_be_worker_or_supply_raw_numeric_target() -> None:
    with pytest.raises(ModelValidationError, match="separate worker"):
        begin(
            supervisor=ACTOR.model_copy(update={"targets": SUPERVISOR.targets}),
            supervisor_target_id="skill:administration",
        )
    state, preparation = begin()
    with pytest.raises(ModelValidationError, match="separate compiled actor"):
        prepare_long_task(
            state,
            preparation.command.model_copy(update={"supervisor_target": 20}),
            RULE,
            ACTOR,
            started_at=0,
        )
    with pytest.raises(ModelValidationError, match="supervision skill"):
        begin(supervisor=SUPERVISOR)


def test_phase_retry_restart_changed_selection_stale_state_and_authority() -> None:
    state, preparation = begin(hours=10)
    check = roll_long_task_check(preparation, rng=RecordedDice((5, 5, 5)))
    after, worker, outcome = select_long_task_check(
        state, preparation, check, rng=RecordedDice(()), system=True
    )
    restored = ResourceState.model_validate_json(after.model_dump_json())
    restored_preparation = LongTaskPreparation.model_validate_json(preparation.model_dump_json())
    assert select_long_task_check(
        restored, restored_preparation, check, rng=RecordedDice(()), system=True
    ) == (restored, worker, outcome)
    with pytest.raises(ConflictError, match="different check"):
        select(restored, preparation, (3, 3, 3))
    with pytest.raises(ConflictError, match="resource state changed"):
        select(state.model_copy(update={"revision": 55}), preparation, (5, 5, 5))
    with pytest.raises(ValidationError, match="captured phase"):
        select_long_task_check(
            state,
            preparation,
            replace(check, effective_target=100),
            rng=RecordedDice(()),
            system=True,
        )
    with pytest.raises(ValidationError, match="engine authority"):
        select_long_task_check(state, preparation, check, rng=RecordedDice(()))


def test_time_must_be_settled_before_opening_original_and_overlap_is_forbidden() -> None:
    state, preparation = begin()
    with pytest.raises(ValidationError, match="elapsed time"):
        prepare_long_task(
            state.model_copy(update={"game_time": 0}),
            preparation.command,
            RULE,
            ACTOR,
            started_at=0,
        )
    state, _, _ = select(state, preparation, (3, 3, 3))
    with pytest.raises(ValidationError, match="already worked"):
        ensure_long_task_available(state, "a", started_at=4 * 3600, seconds=8 * 3600)


def test_repeated_daily_contributions_cannot_bypass_overtime_or_supervisor_commitment() -> None:
    state, preparation = begin(supervisor=SUPERVISOR, supervisor_target_id="skill:administration")
    state, worker, _ = select(state, preparation, (3, 3, 3))
    state, _, _ = select(state, worker, (3, 3, 3))
    for actor_id in ("a", "supervisor"):
        for started_at in (8 * 3600, DAY - 3600):
            with pytest.raises(ValidationError, match="already worked this day"):
                ensure_long_task_available(state, actor_id, started_at=started_at, seconds=3600)
        ensure_long_task_available(state, actor_id, started_at=DAY, seconds=3600)


def test_worker_critical_failure_clamps_destroyed_total_and_retries_without_two_more_dice() -> None:
    state, preparation = begin()
    check = roll_long_task_check(preparation, rng=RecordedDice((6, 6, 6)))
    state, complete, outcome = select_long_task_check(
        state, preparation, check, rng=RecordedDice((6, 6)), system=True
    )
    assert outcome is not None
    assert (outcome.progress, outcome.total_progress, outcome.ruined_hours) == (
        Decimal(0),
        Decimal(0),
        12,
    )
    restored = ResourceState.model_validate_json(state.model_dump_json())
    assert select_long_task_check(
        restored, preparation, check, rng=RecordedDice(()), system=True
    ) == (restored, complete, outcome)


def supervised_resources() -> ResourceState:
    state = resources()
    return state.model_copy(
        update={
            "pools": state.pools
            + tuple(
                pool.model_copy(update={"id": pool.id.replace(":a", ":supervisor")})
                for pool in state.pools
            )
        }
    )


def test_supervisor_overtime_has_own_cost_penalty_and_rest_before_supervision() -> None:
    state, supervisor_ht = begin(
        supervised_resources(),
        hours=10,
        supervisor=SUPERVISOR,
        supervisor_target_id="skill:administration",
    )
    assert supervisor_ht.phase == "supervisor-overtime"
    assert supervisor_ht.check_actor_id == "supervisor" and supervisor_ht.check_target == 12
    state, supervision, result = select(state, supervisor_ht, (6, 6, 6))
    assert result is None and supervision.phase == "supervision"
    assert supervision.check_target == 6
    assert supervision.supervisor_fatigue is not None
    assert supervision.supervisor_fatigue.fp_lost == 6
    assert {pool.id: pool.current for pool in state.pools if pool.id.startswith("fp:")} == {
        "fp:a": 10,
        "fp:supervisor": 4,
    }
    state, worker_ht, result = select(state, supervision, (2, 2, 2))
    assert result is None and worker_ht.phase == "overtime"
    assert worker_ht.check_actor_id == "a" and worker_ht.check_target == 12
    assert worker_ht.check_id != supervisor_ht.check_id
    state, worker, result = select(state, worker_ht, (5, 5, 5))
    assert result is None and worker.check_target == 10
    assert {pool.id: pool.current for pool in state.pools if pool.id.startswith("fp:")} == {
        "fp:a": 7,
        "fp:supervisor": 4,
    }
    state, _, result = select(state, worker, (3, 3, 3))
    assert result is not None and result.progress == 10 and result.fp_lost == 3
    assert tuple(check.total for check in result.checks) == (18, 6, 15, 9)
    ensure_long_task_available(state, "a", started_at=DAY, seconds=3600)
    with pytest.raises(ValidationError, match="exhausted next day"):
        ensure_long_task_available(state, "supervisor", started_at=DAY, seconds=3600)


def test_each_actor_can_use_own_luck_on_overtime_without_sharing_roll_or_cooldown() -> None:
    build, definitions = approved()
    state, preparation = begin(
        supervised_resources(),
        hours=10,
        supervisor=SUPERVISOR,
        supervisor_target_id="skill:administration",
    )
    original = roll_long_task_check(preparation, rng=RecordedDice((6, 6, 6)))
    roll = LuckRoll(
        id=preparation.check_id, actor_id=preparation.check_actor_id, original=original.dice
    )
    luck = LuckState(rolls=(roll,), pending_roll_id=roll.id)

    def use(snapshot: LuckState, actor_id: str, identifier: str) -> LuckState:
        assert snapshot.pending_roll_id is not None
        return apply_luck(
            snapshot,
            LuckCommand(
                id=identifier,
                actor_id=actor_id,
                expected_revision=snapshot.revision,
                roll_id=snapshot.pending_roll_id,
            ),
            build,
            definitions,
            real_time=100,
            seed=SEED,
            authorized_actor_id=actor_id,
            system=True,
        )[0]

    with pytest.raises(ValidationError, match="shared"):
        use(luck, "a", "borrow-worker-luck")
    luck = use(luck, "supervisor", "supervisor-luck")
    selected = luck.rolls[-1].chosen_dice
    assert selected == (1, 2, 4)
    state, supervision, _ = select(state, preparation, (selected[0], selected[1], selected[2]))
    assert supervision.supervisor_fatigue is None
    supervisor_check = LuckRoll(id=supervision.check_id, actor_id="supervisor", original=(6, 6, 6))
    luck = luck.model_copy(
        update={"rolls": luck.rolls + (supervisor_check,), "pending_roll_id": supervisor_check.id}
    )
    with pytest.raises(ValidationError, match="cooling"):
        use(luck, "supervisor", "supervisor-again")
    with pytest.raises(ValidationError, match="shared"):
        use(luck, "a", "borrow-supervisor-roll")
    state, worker_ht, _ = select(state, supervision, (3, 3, 3))
    worker_roll = LuckRoll(
        id=worker_ht.check_id, actor_id=worker_ht.check_actor_id, original=(6, 6, 6)
    )
    luck = luck.model_copy(
        update={"rolls": luck.rolls + (worker_roll,), "pending_roll_id": worker_roll.id}
    )
    luck = use(luck, "a", "worker-luck")
    selected = luck.rolls[-1].chosen_dice
    assert selected == (1, 2, 4)
    state, worker, _ = select(state, worker_ht, (selected[0], selected[1], selected[2]))
    assert worker.fatigue is None and worker.check_target == 13
    assert tuple((receipt.command.actor_id, receipt.available_at) for receipt in luck.receipts) == (
        ("supervisor", 3700),
        ("a", 3700),
    )
    _, _, outcome = select(state, worker, (4, 4, 4))
    assert outcome is not None and outcome.progress == 10 and outcome.fp_lost == 0


def test_captured_modifiers_are_phase_local_and_keep_provenance_through_json_selection() -> None:
    state, initial = begin(
        supervised_resources(),
        hours=10,
        supervisor=SUPERVISOR,
        supervisor_target_id="skill:administration",
    )
    worker = Modifier(-1, "Worker condition", "condition:worker", "1")
    supervisor = Modifier(-2, "Supervisor condition", "condition:supervisor", "1")
    worker_ht = Modifier(1, "Worker Fit", "trait:worker-fit", "1")
    supervisor_ht = Modifier(2, "Supervisor HT equipment", "equipment:supervisor-ht", "1")
    preparation = prepare_long_task(
        state,
        initial.command,
        RULE,
        ACTOR,
        started_at=0,
        supervisor=SUPERVISOR,
        supervisor_target_id="skill:administration",
        worker_modifiers=(worker,),
        supervisor_modifiers=(supervisor,),
        worker_ht_modifiers=(worker_ht,),
        supervisor_ht_modifiers=(supervisor_ht,),
    )
    phase_modifiers = (
        ("supervisor-overtime", supervisor_ht, 14),
        ("supervision", supervisor, 10),
        ("overtime", worker_ht, 13),
        ("worker", worker, 12),  # Includes the selected supervisor's +1.
    )
    outcome: ActivityOutcome | None = None
    for phase, modifier, target in phase_modifiers:
        state = ResourceState.model_validate_json(state.model_dump_json())
        restored = LongTaskPreparation.model_validate_json(preparation.model_dump_json())
        assert restored == preparation and restored.phase == phase
        assert restored.worker_modifiers == (worker,)
        assert restored.supervisor_modifiers == (supervisor,)
        assert restored.worker_ht_modifiers == (worker_ht,)
        assert restored.supervisor_ht_modifiers == (supervisor_ht,)
        check = roll_long_task_check(restored, rng=RecordedDice((3, 3, 3)))
        assert check.base_target == 12 and check.effective_target == target
        assert check.modifiers[0] == modifier
        assert tuple(
            m
            for m in check.modifiers
            if m.source_id.startswith(("condition:", "trait:", "equipment:"))
        ) == (modifier,)
        state, preparation, outcome = select_long_task_check(
            state,
            restored,
            check,
            rng=RecordedDice(()),
            system=True,
        )
    assert outcome is not None and outcome.progress == 10
    assert tuple(check.modifiers[0] for check in outcome.checks) == (
        supervisor_ht,
        supervisor,
        worker_ht,
        worker,
    )
