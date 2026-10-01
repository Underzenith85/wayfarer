"""B356-357/B422 actual injury, fatigue and secondary consequences."""

from decimal import Decimal

import pytest
from test_campaign_activities import advance, fatigue

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.campaign.activities import (
    ActivityActor,
    ActivityOutcome,
    ExtraEffortRule,
    PerformActivity,
    apply_activity,
)
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def initial(*, fp: int = 10, machine: bool = False) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=10,
                maximum=10,
                injury=InjuryStatus(
                    profile_id="gurps-basic-set-4e-2004", anatomy="human", machine=machine
                ),
            ),
            Pool(
                id="fp:a",
                current=fp,
                maximum=10,
                fatigue=FatigueStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
        )
    )


def execute(
    state: ResourceState,
    dice: tuple[int, ...],
    rule: ExtraEffortRule,
    *,
    identity: str = "effort",
    authority: bool = True,
) -> tuple[ResourceState, ActivityOutcome]:
    actor = ActivityActor(
        actor_id="a", basic_lift=20, basic_move=5, ht=12, will=12, current_fp=state.pools[1].current
    )
    command = PerformActivity(
        id=identity,
        actor_id="a",
        expected_revision=state.revision,
        activity_id=rule.id,
        seconds=15
        if rule.task == "running"
        else 1
        if rule.task in {"jumping", "instant", "throwing"}
        else 60,
    )
    return apply_activity(
        state,
        command,
        rule,
        actor,
        rng=RecordedDice(dice),
        advance=advance,
        lose_fatigue=fatigue,
        system=authority,
    )


@pytest.mark.parametrize(
    "dice,progress,cost", [((3, 3, 3), 10, 1), ((5, 5, 5), 0, 1), ((1, 1, 1), 10, 0)]
)
def test_effort_success_failure_and_critical_success_costs(
    dice: tuple[int, ...], progress: int, cost: int
) -> None:
    rule = ExtraEffortRule(
        id="lift", requested_percent=10, task="lifting", critical_failure_consequence="back"
    )
    after, outcome = execute(initial(), dice, rule)
    assert outcome.progress == Decimal(progress)
    assert outcome.fp_lost == cost and after.pools[1].current == 10 - cost
    assert after.pools[0].current == 10
    assert outcome.checks[0].effective_target == 11


def test_critical_injury_rest_only_and_once_only_secondary_procedure() -> None:
    rule = ExtraEffortRule(
        id="lift",
        requested_percent=10,
        task="lifting",
        ordinary_fp_cost=2,
        critical_failure_consequence="back",
    )
    before = initial()
    after, outcome = execute(before, (6, 6, 6, 5, 5, 5, 2), rule)
    assert outcome.progress == 0 and outcome.hp_lost == 3 and outcome.fp_lost == 3
    hp = after.pools[0]
    assert hp.current == 7 and after.pools[1].current == 7
    assert hp.injury is not None and hp.injury.rest_only_injury == 3
    condition = hp.injury.lasting_injuries[0]
    assert condition.kind == "bad-back" and condition.duration == "lasting"
    assert condition.recovery_at == 60 * 86400
    assert len(outcome.checks) == 2 and outcome.checks[1].effective_target == 12
    assert restore_hp(after, hp, 3, kind="first-aid") == (hp, 0)
    healed, amount = restore_hp(after, hp, 3, kind="natural")
    assert amount == 3 and healed.current == 10
    assert healed.injury is not None and healed.injury.rest_only_injury == 0
    restored = ResourceState.model_validate_json(after.model_dump_json())
    actor = ActivityActor(actor_id="a", basic_lift=20, basic_move=5, ht=12, will=12)
    command = PerformActivity(
        id="effort", actor_id="a", expected_revision=0, activity_id="lift", seconds=60
    )
    assert apply_activity(
        restored,
        command,
        rule,
        actor,
        rng=RecordedDice(()),
        advance=advance,
        lose_fatigue=fatigue,
        system=True,
    ) == (restored, outcome)
    with pytest.raises(ConflictError):
        apply_activity(
            restored,
            command.model_copy(update={"seconds": 61}),
            rule,
            actor,
            rng=RecordedDice(()),
            advance=advance,
            lose_fatigue=fatigue,
            system=True,
        )


@pytest.mark.parametrize("secondary,permanent", [((3, 3, 3), False), ((6, 6, 6), True)])
def test_natural_eighteen_avoidance_or_permanent_leg(
    secondary: tuple[int, ...], permanent: bool
) -> None:
    rule = ExtraEffortRule(
        id="run",
        requested_percent=10,
        task="running",
        injury_location="left-leg",
        temporary_disadvantage="crippled-leg",
        critical_failure_consequence="leg",
    )
    after, outcome = execute(initial(), (6, 6, 6) + secondary, rule)
    hp = after.pools[0]
    assert hp.current == 9 and outcome.hp_lost == 1
    assert hp.injury is not None
    assert bool(hp.injury.lasting_injuries) == permanent
    if permanent:
        assert hp.injury.lasting_injuries[0].duration == "permanent"
        assert hp.injury.lasting_injuries[0].location == "left-leg"
    assert len(outcome.checks) == 2


def test_current_fatigue_penalty_and_authority_fail_before_dice() -> None:
    rule = ExtraEffortRule(
        id="jump",
        requested_percent=10,
        task="jumping",
        injury_location="right-foot",
        temporary_disadvantage="crippled-leg",
        critical_failure_consequence="leg",
    )
    after, outcome = execute(initial(fp=8), (3, 3, 3), rule)
    assert outcome.checks[0].effective_target == 8
    assert after.pools[1].current == 7
    for state, authority in ((initial(), False), (initial(machine=True), True)):
        with pytest.raises(ValidationError):
            execute(state, (), rule, authority=authority)


def test_nonpositive_fatigue_settles_failed_permission_without_effort_cost() -> None:
    rule = ExtraEffortRule(
        id="lift", requested_percent=10, task="lifting", critical_failure_consequence="back"
    )
    after, outcome = execute(initial(fp=0), (5, 5, 5), rule)
    assert after.pools[1].fatigue is not None and after.pools[1].fatigue.collapsed
    assert after.pools[1].current == 0 and after.pools[0].current == 10
    assert outcome.fp_lost == outcome.hp_lost == 0
    assert outcome.progress == 0 and outcome.elapsed_seconds == after.game_time == 0
    assert len(outcome.checks) == 1 and outcome.checks[0].effective_target == 12


def test_leg_overexertion_loses_the_entire_fp_cost_without_amputation() -> None:
    rule = ExtraEffortRule(
        id="run",
        requested_percent=10,
        task="running",
        injury_location="left-leg",
        temporary_disadvantage="crippled-leg",
        ordinary_fp_cost=5,
        critical_failure_consequence="leg",
    )
    # Critical failure by ten, ordinary canonical major-wound HT success; no natural18 secondary roll.
    actor_state = initial()
    actor = ActivityActor(actor_id="a", basic_lift=20, basic_move=5, ht=12, will=5)
    command = PerformActivity(
        id="run", actor_id="a", expected_revision=0, activity_id="run", seconds=15
    )
    after, outcome = apply_activity(
        actor_state,
        command,
        rule,
        actor,
        rng=RecordedDice((5, 5, 5, 3, 3, 3)),
        advance=advance,
        lose_fatigue=fatigue,
        system=True,
    )
    assert outcome.hp_lost == outcome.fp_lost == 6
    assert after.pools[0].current == after.pools[1].current == 4
    assert after.pools[0].injury is not None
    injury = after.pools[0].injury.lasting_injuries[0]
    assert (
        injury.kind == "crippled" and injury.duration == "pending" and injury.location == "left-leg"
    )
