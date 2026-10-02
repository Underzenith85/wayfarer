"""Ordinary task capture preserves the original roll and applies real consequences."""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pytest
from test_actions import engine, seed, world
from test_skills import compiler, draft
from test_symptom_attribute_consumers import penalize

from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PROTOTYPE_PACKAGE
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.effects import Effect, Operation
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import (
    ActionRules,
    ActorSetup,
    CheckRule,
    Inspect,
    PlayActor,
    PlayState,
    Social,
    Wait,
)
from wayfarer.engine.simulation.events import action_result
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def ordinary_command(kind: Literal["inspect", "social"], revision: int = 0) -> Inspect | Social:
    if kind == "inspect":
        return Inspect(id="task", actor_id="a", target_id="chest", expected_revision=revision)
    return Social(id="task", actor_id="a", target_id="b", expected_revision=revision)


@pytest.mark.parametrize("kind,fact", [("inspect", "clue"), ("social", "promise")])
@pytest.mark.parametrize(
    "faces,outcome",
    [
        ((1, 1, 1), Outcome.CRITICAL_SUCCESS),
        ((3, 3, 3), Outcome.SUCCESS),
        ((4, 4, 4), Outcome.FAILURE),
        ((6, 6, 6), Outcome.CRITICAL_FAILURE),
    ],
)
def test_original_capture_and_selected_consequence(
    kind: Literal["inspect", "social"],
    fact: str,
    faces: tuple[int, int, int],
    outcome: Outcome,
) -> None:
    base = engine()
    reducer = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"fatigue_cost": 1})
    )
    initial = seed(reducer)
    # The host settles prerequisite time before exposing the original roll.
    state, _ = reducer.resolve(
        initial, Wait(id="prerequisite", actor_id="a", expected_revision=0, ticks=2)
    )
    command = ordinary_command(kind, state.revision)
    before = state.model_dump_json()
    preparation = reducer.prepare_task_check(state, command)
    original_dice = RecordedDice((6, 6, 6))
    original = preparation.check(rng=original_dice)
    assert original.outcome is Outcome.CRITICAL_FAILURE and original_dice.exhausted()
    assert state.model_dump_json() == before
    assert state.world.knowledge == () and state.resources.game_time == 2
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    selected_dice = RecordedDice(faces)
    selected = preparation.check(rng=selected_dice)
    assert selected.outcome is outcome and selected_dice.exhausted()
    updated, events = reducer.resolve_prepared_task(state, command, preparation, selected)
    result = action_result(events)
    assert result.check == selected
    assert result.derived == preparation.derived and result.dependencies == preparation.dependencies
    assert result.revealed_fact_ids == ((fact,) if outcome.succeeded else ())
    assert updated.world.knowledge == ((("a", fact),) if outcome.succeeded else ())
    if outcome.succeeded:
        assert fact in {value.id for value in updated.world.perspective("a").facts}
    assert updated.resources.game_time == 2 and updated.resources.fired == ("expiry",)
    assert next(p.current for p in updated.resources.pools if p.id == "fp:a") == 9
    assert updated.revision == state.revision + 1 == updated.resources.revision
    assert state.model_dump_json() == before
    with pytest.raises(ConflictError, match="revision"):
        reducer.resolve_prepared_task(updated, command, preparation, selected)


def equipped_engine() -> ActionEngine:
    reducer = engine(equipment="tool")
    reducer.resources.specs["tool"] = reducer.resources.specs["tool"].model_copy(
        update={
            "effects": (
                Effect(
                    "lens",
                    "attribute:iq",
                    Operation.ADD,
                    Decimal(2),
                    "tool",
                    PROTOTYPE_PACKAGE.version,
                ),
            )
        }
    )
    return ActionEngine(reducer.reviewer, reducer.resources, reducer.rules)


def test_preparation_requires_equipment_but_captured_roll_survives_later_context_changes() -> None:
    reducer = equipped_engine()
    state = seed(reducer)
    command = ordinary_command("inspect")
    with pytest.raises(ValidationError, match="check.equipment_required"):
        reducer.prepare_task_check(state, command)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "game_time": 2,
                    "items": tuple(
                        item.model_copy(update={"ready": True, "equipped": True})
                        if item.id == "tool"
                        else item
                        for item in state.resources.items
                    ),
                }
            )
        }
    )
    preparation = reducer.prepare_task_check(state, command)
    selected = preparation.check(rng=RecordedDice((4, 4, 4)))
    assert preparation.target == 13 and selected.effective_target == 12
    assert selected.outcome is Outcome.SUCCESS
    assert preparation.dependencies[0].value == 12
    assert preparation.dependencies[0].explanations[0].source_id == "tool"

    # Canonical changes after the roll must neither rescore it nor be overwritten.
    current = state.model_copy(
        update={
            "revision": 1,
            "world": state.world.learn("a", "promise"),
            "actors": (state.actors[0].model_copy(update={"approval": None}),),
            "resources": state.resources.model_copy(
                update={
                    "revision": 1,
                    "items": tuple(
                        item.model_copy(update={"ready": False, "equipped": False})
                        if item.id == "tool"
                        else item
                        for item in state.resources.items
                    ),
                }
            ),
        }
    )
    current_command = command.model_copy(update={"id": "accept", "expected_revision": 1})
    with pytest.raises(ValidationError, match="approval_required"):
        reducer.prepare_task_check(current, current_command)
    updated, events = reducer.resolve_prepared_task(current, current_command, preparation, selected)
    assert action_result(events).check == selected
    assert set(updated.world.knowledge) == {("a", "clue"), ("a", "promise")}
    assert updated.resources.items == current.resources.items
    assert updated.actors == current.actors and updated.resources.game_time == 2


@pytest.mark.parametrize("automatic", [False, True])
def test_preparation_requires_current_approval_and_authored_skill(automatic: bool) -> None:
    reducer = engine(automatic=automatic)
    state = seed(reducer)
    if automatic:
        actor = state.actors[0]
        proposal = actor.proposal.model_copy(
            update={
                "draft": actor.proposal.draft.model_copy(
                    update={
                        "purchases": tuple(
                            p
                            for p in actor.proposal.draft.purchases
                            if p.definition_id != "skill:observation"
                        )
                    }
                )
            }
        )
        approval = reducer.reviewer.approve(proposal, campaign_id="c", actor_id="a", revision=0)
        state = state.model_copy(
            update={
                "actors": (actor.model_copy(update={"proposal": proposal, "approval": approval}),),
                "approvals": (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": (
                            state.resources.owners[0].model_copy(
                                update={
                                    "definitions": tuple(
                                        p.definition_id for p in proposal.draft.purchases
                                    )
                                }
                            ),
                        )
                    }
                ),
            }
        )
    before = state.model_dump_json()
    with pytest.raises(
        ValidationError, match="check.skill_required" if automatic else "approval_required"
    ):
        reducer.prepare_task_check(state, ordinary_command("inspect"))
    assert state.model_dump_json() == before


def gurps_state() -> tuple[ActionEngine, PlayState]:
    profile, character_compiler = GURPS_BASIC_PROFILE, compiler()
    reviewer = PowerReviewer(
        character_compiler,
        PowerPolicy(id="power", version=1, automatic_approval=True),
        frozenset({"gm"}),
    )
    resources = ResourceEngine(
        world(), profile.catalog, profile.rules, character_compiler.policy, ()
    )
    checks = (
        CheckRule(
            id="inspect",
            action="inspect",
            target_id="chest",
            definition_id="skill:observation",
            package_id=profile.packages[0].id,
            package_version="0.3.0",
            reveal_fact_ids=("clue",),
            darkness_penalty=-2,
        ),
        CheckRule(
            id="social",
            action="social",
            target_id="b",
            definition_id="skill:diplomacy",
            package_id=profile.packages[0].id,
            package_version="0.3.0",
            reveal_fact_ids=("promise",),
        ),
    )
    reducer = ActionEngine(reviewer, resources, ActionRules(id="skills", version=1, checks=checks))
    setup = ActorSetup(
        actor_id="a",
        proposal=CharacterProposal(draft=draft(**{"secondary:per": 14, "skill:diplomacy": 4})),
        aware_of=("chest", "b"),
    )
    approval = reviewer.approve(setup.proposal, campaign_id="c", actor_id="a", revision=0)
    state = PlayState(
        campaign_id="c",
        configuration_digest=reducer.digest,
        world=world(),
        resources=ResourceState(
            owners=(
                Owner(
                    actor_id="a",
                    capacity=100,
                    definitions=tuple(p.definition_id for p in setup.proposal.draft.purchases),
                ),
            ),
            pools=(
                Pool(id="hp:a", current=10, maximum=10),
                Pool(id="fp:a", current=10, maximum=10),
            ),
        ),
        actors=(PlayActor(**setup.model_dump(), approval=approval),),
        approvals=(approval,),
    )
    return reducer, state


@pytest.mark.parametrize("correct_attributes", [False, True])
def test_gurps_defaults_darkness_and_symptoms_keep_exact_targets(correct_attributes: bool) -> None:
    reducer, state = gurps_state()
    state = penalize(state, "a", "iq")
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "symptom_effects": state.resources.symptom_effects
                    + (
                        SymptomEffect(
                            id="cough",
                            pool_id="hp:a",
                            source_id="cough",
                            actor_id="a",
                            active=True,
                            spec=SymptomSpec(kind="coughing"),
                        ),
                    ),
                    "symptom_debts": state.resources.symptom_debts
                    + (SymptomDebt(id="cough", pool_id="hp:a", source_id="cough", remaining=6),),
                }
            )
        }
    )
    cases: tuple[tuple[Literal["inspect", "social"], int, int], ...] = (
        ("inspect", 5, 3),
        ("social", 6, 5),
    )
    for kind, base_target, target in cases:
        command = ordinary_command(kind)
        preparation = reducer.prepare_task_check(
            state, command, correct_symptom_attributes=correct_attributes
        )
        selected = preparation.check(rng=RecordedDice((2, 2, 2)))
        assert preparation.target == base_target and selected.effective_target == target
        if kind == "inspect":
            assert [(m.reason, m.value) for m in preparation.modifiers] == [
                ("inspect", 0),
                ("inspect:darkness", -2),
            ]
        else:
            assert [(m.reason, m.value) for m in preparation.modifiers] == [
                ("social", 0),
                ("Symptoms coughing", -1),
            ]
        _, ordinary_events = reducer.resolve(
            state,
            command,
            rng=RecordedDice((2, 2, 2)),
            advance_time=False,
            correct_symptom_attributes=correct_attributes,
        )
        _, prepared_events = reducer.resolve_prepared_task(state, command, preparation, selected)
        assert prepared_events == ordinary_events


def test_prepared_completion_rejects_mismatched_context_and_forged_scoring() -> None:
    reducer = engine()
    state = seed(reducer)
    command = ordinary_command("inspect")
    preparation = reducer.prepare_task_check(state, command)
    selected = preparation.check(rng=RecordedDice((3, 3, 3)))
    for forged in (
        replace(selected, base_target=99),
        replace(selected, effective_target=99),
        replace(selected, margin=99),
        replace(selected, outcome=Outcome.FAILURE),
        replace(selected, rules_version="forged"),
        replace(selected, dice=(0, 3, 3)),
    ):
        with pytest.raises(ValidationError):
            reducer.resolve_prepared_task(state, command, preparation, forged)
    for changed in (
        replace(preparation, campaign_id="other"),
        replace(preparation, actor_id="b"),
        replace(preparation, action="social"),
        replace(preparation, target_id="b"),
        replace(preparation, configuration_digest="changed"),
    ):
        with pytest.raises(ValidationError, match="does not match"):
            reducer.resolve_prepared_task(state, command, changed, selected)
    with pytest.raises(ValidationError, match="does not match"):
        reducer.resolve_prepared_task(
            state, command.model_copy(update={"hypothetical": True}), preparation, selected
        )
    assert state.world.knowledge == () and state.revision == 0


def test_prepared_completion_cannot_overdraw_fatigue_or_charge_a_paid_cost_twice() -> None:
    base = engine()
    reducer = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"fatigue_cost": 1})
    )
    state = seed(reducer)
    command = ordinary_command("inspect")
    preparation = reducer.prepare_task_check(state, command)
    selected = preparation.check(rng=RecordedDice((3, 3, 3)))
    current = state.model_copy(
        update={
            "revision": 1,
            "resources": state.resources.model_copy(
                update={
                    "revision": 1,
                    "pools": tuple(
                        pool.model_copy(update={"current": 0}) if pool.id == "fp:a" else pool
                        for pool in state.resources.pools
                    ),
                }
            ),
        }
    )
    current_command = command.model_copy(update={"expected_revision": 1})
    before = current.model_dump_json()
    with pytest.raises(ValidationError, match="resource.fatigue"):
        reducer.prepare_task_check(current, current_command)
    paid_preparation = reducer.prepare_task_check(
        current, current_command, fatigue_already_paid=True
    )
    assert paid_preparation.check(rng=RecordedDice((3, 3, 3))) == selected
    with pytest.raises(ConflictError, match="fatigue resources changed"):
        reducer.resolve_prepared_task(current, current_command, preparation, selected)
    assert current.model_dump_json() == before
    # If the host already settled the cost before the original, acceptance still
    # finishes its real consequence after the actor spends their remaining FP.
    updated, events = reducer.resolve_prepared_task(
        current, current_command, preparation, selected, fatigue_already_paid=True
    )
    assert action_result(events).check == selected
    assert updated.world.knowledge == (("a", "clue"),)
    assert next(pool.current for pool in updated.resources.pools if pool.id == "fp:a") == 0
    assert PlayState.model_validate_json(updated.model_dump_json()) == updated
