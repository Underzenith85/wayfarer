"""Acceptance evidence for #689 (B346 and B350-356)."""

from decimal import Decimal

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.activities import (
    ActivityActor,
    ActivityOutcome,
    BreathRule,
    DiggingRule,
    ExtraEffortRule,
    LongTaskRule,
    PerformActivity,
    RunningRule,
    apply_activity,
    gravity_effects,
)
from wayfarer.engine.simulation.resources import ResourceState


def advance(state: ResourceState, to: int, _parent: str) -> ResourceState:
    return state.model_copy(update={"revision": state.revision + 1, "game_time": to})


def fatigue(state: ResourceState, _amount: int, _parent: str, _actor: str) -> ResourceState:
    return state.model_copy(update={"revision": state.revision + 1})


ACTOR = ActivityActor(
    actor_id="a",
    basic_lift=20,
    basic_move=5,
    ht=12,
    will=12,
    targets=(("skill:carpentry", 12), ("skill:running", 14)),
)


def perform(
    state: ResourceState,
    identity: str,
    rule: LongTaskRule | DiggingRule | BreathRule | RunningRule | ExtraEffortRule,
    seconds: int,
    dice: tuple[int, ...] = (),
    *,
    supervisor_target: int | None = None,
) -> tuple[ResourceState, ActivityOutcome]:
    return apply_activity(
        state,
        PerformActivity(
            id=identity,
            actor_id="a",
            expected_revision=state.revision,
            activity_id=rule.id,
            seconds=seconds,
            supervisor_target=supervisor_target,
        ),
        rule,
        ACTOR,
        rng=RecordedDice(dice),
        advance=advance,
        lose_fatigue=fatigue,
        system=True,
    )


def test_long_task_progress_supervision_restart_and_replay() -> None:
    rule = LongTaskRule(id="boat", target_id="skill:carpentry", required_man_hours=16)
    before = ResourceState()
    command = PerformActivity(
        id="day-1",
        actor_id="a",
        expected_revision=0,
        activity_id=rule.id,
        seconds=8 * 3600,
        supervisor_target=12,
    )
    worked, result = apply_activity(
        before,
        command,
        rule,
        ACTOR,
        rng=RecordedDice((3, 3, 3, 3, 3, 3)),
        advance=advance,
        lose_fatigue=fatigue,
        system=True,
    )
    assert (result.progress, result.completed, worked.game_time) == (Decimal(8), False, 28800)
    assert apply_activity(
        worked,
        command,
        rule,
        ACTOR,
        rng=RecordedDice(()),
        advance=advance,
        lose_fatigue=fatigue,
        system=True,
    ) == (worked, result)
    restored = ResourceState.model_validate_json(worked.model_dump_json())
    finished, result = perform(restored, "day-2", rule, 8 * 3600, (3, 3, 3))
    assert result.completed and result.total_progress == 16
    assert finished.game_time == 16 * 3600


def test_digging_breath_running_and_extra_effort() -> None:
    state, dug = perform(
        ResourceState(),
        "dig",
        DiggingRule(
            id="ditch",
            soil="ordinary",
            tool="shovel",
            required_cubic_feet=Decimal(20),
        ),
        3600,
    )
    assert (dug.progress, dug.fp_lost, dug.completed) == (Decimal(20), 2, True)

    breath = BreathRule(id="dive", exertion="heavy", preparation="deep-breath")
    state, held = perform(state, "hold", breath, 12)
    assert not held.suffocating
    state, held = perform(state, "hold-more", breath, 2)
    assert held.suffocating and held.fp_lost == 2
    assert held.consequence == "canonical-suffocation"

    state, ran = perform(
        state,
        "run",
        RunningRule(id="sprint", pace="sprint"),
        15,
        (6, 6, 6),
    )
    assert (ran.move, ran.progress, ran.fp_lost) == (6, Decimal(89), 1)
    state, effort = perform(
        state,
        "effort",
        ExtraEffortRule(
            id="heave",
            requested_percent=10,
            critical_failure_consequence="strained-back",
        ),
        1,
        (6, 6, 6),
    )
    assert effort.fp_lost == 1 and effort.consequence == "strained-back"


def test_gravity_adjustments_preserve_base_values() -> None:
    low = gravity_effects(
        Decimal("0.2"),
        body_weight=Decimal(150),
        gear_weight=Decimal(20),
        strength=10,
        basic_move=5,
        experienced=True,
    )
    assert low["effective_load"] == Decimal(4)
    assert low["jump_multiplier"] == Decimal(5)
    high = gravity_effects(
        Decimal("1.4"),
        body_weight=Decimal(150),
        gear_weight=Decimal(20),
        strength=10,
        basic_move=5,
    )
    assert high["effective_load"] == Decimal(68)
    assert high["dx_penalty"] == -2
    assert high["iq_ht_fp_penalty"] == -1
