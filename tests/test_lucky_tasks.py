"""B66 changes real task progress before commit, with atomic replay and authority."""

from decimal import Decimal
from typing import Literal

import pytest
from test_campaign_activities import ACTOR, advance, fatigue
from test_luck import SEED, approved

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.campaign.activities import (
    ActivityOutcome,
    LongTaskRule,
    PerformActivity,
)
from wayfarer.engine.simulation.campaign.luck import apply_lucky_task
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.luck import LuckCommand, LuckRoll, LuckState
from wayfarer.errors import ConflictError, ValidationError


def test_ordinary_luck_changes_task_progress_and_persists_once() -> None:
    build, definitions = approved()
    command = PerformActivity(
        id="work", actor_id="inventor", expected_revision=0, activity_id="boat", seconds=28800
    )
    use = LuckCommand(id="use", actor_id="inventor", expected_revision=0, roll_id="work")
    rule = LongTaskRule(id="boat", target_id="skill:carpentry", required_man_hours=8)
    actor = ACTOR.model_copy(update={"actor_id": "inventor"})
    before = ResourceState()
    luck = LuckState(
        rolls=(LuckRoll(id="work", actor_id="inventor", original=(6, 6, 6)),),
        pending_roll_id="work",
    )

    def invoke(
        resources: ResourceState, snapshot: LuckState, *, system: bool = True
    ) -> tuple[ResourceState, LuckState, ActivityOutcome]:
        return apply_lucky_task(
            resources,
            snapshot,
            command,
            use,
            rule,
            actor,
            build,
            definitions,
            advance=advance,
            lose_fatigue=fatigue,
            real_time=100,
            seed=SEED,
            authorized_actor_id="inventor",
            system=system,
        )

    with pytest.raises(ValidationError, match="authority"):
        invoke(before, luck, system=False)
    after, used, outcome = invoke(before, luck)
    assert outcome.progress == Decimal(8)
    assert outcome.completed and after.game_time == 28800
    assert outcome.checks[0].dice == used.receipts[0].attempts[used.receipts[0].chosen_index]
    assert used.receipts[0].available_at == 3700
    restored = ResourceState.model_validate_json(after.model_dump_json())
    restored_luck = LuckState.model_validate_json(used.model_dump_json())
    assert invoke(restored, restored_luck) == (restored, restored_luck, outcome)
    with pytest.raises(ConflictError, match="already committed"):
        invoke(after, luck)
    assert before.game_time == 0 and not luck.receipts


def test_invalid_task_does_not_spend_luck() -> None:
    build, definitions = approved()
    luck = LuckState(
        rolls=(LuckRoll(id="work", actor_id="inventor", original=(6, 6, 6)),),
        pending_roll_id="work",
    )
    with pytest.raises(ValidationError, match="whole hours"):
        apply_lucky_task(
            ResourceState(),
            luck,
            PerformActivity(
                id="work", actor_id="inventor", expected_revision=0, activity_id="boat", seconds=1
            ),
            LuckCommand(id="use", actor_id="inventor", expected_revision=0, roll_id="work"),
            LongTaskRule(id="boat", target_id="skill:carpentry", required_man_hours=8),
            ACTOR.model_copy(update={"actor_id": "inventor"}),
            build,
            definitions,
            advance=advance,
            lose_fatigue=fatigue,
            real_time=100,
            seed=SEED,
            authorized_actor_id="inventor",
            system=True,
        )
    assert not luck.receipts


def modified_build(modifiers: tuple[str, ...]) -> tuple[ValidatedBuild, dict[str, RuleDefinition]]:
    from test_mundane_traits import runtime_compiler
    from test_statistics import gurps_draft

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.rules.traits.base import TraitOptions

    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(
            Purchase(
                definition_id="trait:advantage:luck",
                trait=TraitOptions(parameters=(("point-cost", 15),), modifiers=modifiers),
            )
        )
    )
    assert result.build is not None, result.diagnostics
    return result.build, dict(compiler.definitions)


@pytest.mark.parametrize(
    "modifiers,original,task_class",
    [
        (("active",), None, "job"),
        (("aspected-job",), (6, 6, 6), "job"),
        (("active", "aspected-job"), None, "job"),
    ],
)
def test_source_limited_luck_changes_actual_task(
    modifiers: tuple[str, ...], original: tuple[int, ...] | None, task_class: Literal["job"]
) -> None:
    build, definitions = modified_build(modifiers)
    baseline, _ = approved()
    expected_cost = {("active",): 9, ("aspected-job",): 12, ("active", "aspected-job"): 6}[
        modifiers
    ]
    assert build.spent == baseline.spent - 15 + expected_cost
    luck = LuckState(
        rolls=(LuckRoll(id="work", actor_id="inventor", original=original, task_class=task_class),),
        pending_roll_id="work",
    )
    after, used, outcome = apply_lucky_task(
        ResourceState(),
        luck,
        PerformActivity(
            id="work", actor_id="inventor", expected_revision=0, activity_id="boat", seconds=28800
        ),
        LuckCommand(id="use", actor_id="inventor", expected_revision=0, roll_id="work"),
        LongTaskRule(id="boat", target_id="skill:carpentry", required_man_hours=8),
        ACTOR.model_copy(update={"actor_id": "inventor"}),
        build,
        definitions,
        advance=advance,
        lose_fatigue=fatigue,
        real_time=0,
        seed=SEED,
        authorized_actor_id="inventor",
        system=True,
    )
    assert after.game_time == 28800 and outcome.completed
    assert used.receipts[0].available_at == 3600
    assert len(used.receipts[0].attempts) == 3


@pytest.mark.parametrize(
    "modifiers,original,task_class,reason",
    [
        (("active",), (6, 6, 6), "job", "before dice"),
        (("aspected-combat",), (6, 6, 6), "job", "outside"),
        (("defensive",), (6, 6, 6), "job", "failed defense"),
    ],
)
def test_source_limitations_reject_before_spending(
    modifiers: tuple[str, ...], original: tuple[int, ...], task_class: Literal["job"], reason: str
) -> None:
    from wayfarer.engine.simulation.traits.luck import apply_luck

    build, definitions = modified_build(modifiers)
    luck = LuckState(
        rolls=(LuckRoll(id="work", actor_id="inventor", original=original, task_class=task_class),),
        pending_roll_id="work",
    )
    with pytest.raises(ValidationError, match=reason):
        apply_luck(
            luck,
            LuckCommand(id="use", actor_id="inventor", expected_revision=0, roll_id="work"),
            build,
            definitions,
            real_time=0,
            seed=SEED,
            authorized_actor_id="inventor",
            system=True,
        )
    assert not luck.receipts
