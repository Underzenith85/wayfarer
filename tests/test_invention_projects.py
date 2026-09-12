"""Independent ordinary-invention expectations derived from Campaigns B473-475."""

import pytest
from test_actions import engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.projects.invention_transitions import (
    BeginInventionWork,
    CreateInvention,
    InventionOutcome,
    SettleInventionWork,
    apply_invention,
)
from wayfarer.engine.simulation.projects.inventions import (
    InventionBlueprint,
    InventionPhase,
    InventionRules,
    MaterialRequirement,
    StageRequirement,
)
from wayfarer.engine.simulation.resources import Advance, Pool, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def rules(*, facility_cost: int = 10, material_quantity: int = 2) -> InventionRules:
    return InventionRules(
        id="ordinary-inventions",
        version=1,
        blueprints=(
            InventionBlueprint(
                id="field-scanner",
                title="Field scanner",
                concept="A portable scanner using an independently authored physical principle.",
                invention_skill_id="skill:observation",
                related_skill_ids=("skill:diplomacy",),
                operation_skill_id="skill:observation",
                complexity="simple",
                novelty="variant",
                novelty_modifier=5,
                facility_definition_id="tool",
                funding_pool_id="money:a",
                facility_cost=facility_cost,
                concept_work_seconds=10,
                prototype=StageRequirement(
                    work_seconds=20,
                    money=20,
                    materials=(
                        MaterialRequirement(definition_id="potion", quantity=material_quantity),
                    ),
                ),
                testing=StageRequirement(work_seconds=30),
                production=StageRequirement(
                    work_seconds=40,
                    money=5,
                    materials=(MaterialRequirement(definition_id="potion", quantity=1),),
                ),
                target_copies=1,
                native_tl=8,
                inventor_tl=8,
            ),
        ),
    )


def setup(
    dice: tuple[int, ...] = (), *, facility_cost: int = 10, material_quantity: int = 2
) -> tuple[ActionEngine, RulesContext, PlayState]:
    base = engine()
    action_rules = ActionRules(
        id="actions",
        version=1,
        checks=base.rules.checks,
        consumables=base.rules.consumables,
        inventions=rules(facility_cost=facility_cost, material_quantity=material_quantity),
    )
    reducer = ActionEngine(base.reviewer, base.resources, action_rules)
    state = seed(reducer)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": state.resources.pools + (Pool(id="money:a", current=100, maximum=100),)
                }
            )
        }
    )
    runtime = RulesContext(
        rng=RecordedDice(dice),
        resources=reducer.resources,
        reviewer=reducer.reviewer,
        rules=reducer.rules,
        combat=reducer.combat,
    )
    reducer.validate(state)
    return reducer, runtime, state


def advance(reducer: ActionEngine, state: PlayState, to: int, identifier: str) -> PlayState:
    resources = reducer.resources.apply(
        state.resources,
        Advance(
            id=identifier,
            actor_id="a",
            expected_revision=state.revision,
            to=to,
        ),
        system=True,
        rng=RecordedDice(()),
    )
    return state.model_copy(update={"revision": resources.revision, "resources": resources})


def create(runtime: RulesContext, state: PlayState) -> PlayState:
    state, outcome = apply_invention(
        runtime,
        state,
        CreateInvention(
            id="create",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            blueprint_id="field-scanner",
        ),
        system=True,
    )
    assert outcome.status == "created"
    return state


def complete_phase(
    reducer: ActionEngine,
    runtime: RulesContext,
    state: PlayState,
    phase: InventionPhase,
    identifier: str,
) -> tuple[PlayState, InventionOutcome]:
    command = BeginInventionWork(
        id="begin-" + identifier,
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        phase=phase,
    )
    state, _ = apply_invention(runtime, state, command, system=True)
    project = state.resources.inventions[0]
    assert project.active_work is not None
    state = advance(reducer, state, project.active_work.due, "time-" + identifier)
    settle = SettleInventionWork(
        id="settle-" + identifier,
        actor_id="a",
        expected_revision=state.revision,
        project_id=project.id,
        work_id=project.active_work.id,
    )
    state, outcome = apply_invention(runtime, state, settle, system=True)
    return state, outcome


def test_phases_costs_and_unbound_product_are_persisted_monotonically() -> None:
    reducer, runtime, state = setup((1, 1, 1, 1, 1, 1, 1, 1, 1))
    state = create(runtime, state)
    state, _ = complete_phase(reducer, runtime, state, "concept-design", "concept")
    assert state.resources.inventions[0].phase == "prototype"

    prototype_begin = BeginInventionWork(
        id="begin-prototype",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        phase="prototype",
    )
    state, spent = apply_invention(runtime, state, prototype_begin, system=True)
    assert (spent.money_spent, spent.materials_spent[0].quantity) == (30, 2)
    assert next(pool for pool in state.resources.pools if pool.id == "money:a").current == 70
    assert next(item for item in state.resources.items if item.id == "potions").quantity == 3
    restored = PlayState.model_validate_json(state.model_dump_json())
    replayed, replay = apply_invention(
        RulesContext(
            rng=RecordedDice(()),
            resources=reducer.resources,
            reviewer=reducer.reviewer,
            rules=reducer.rules,
            combat=reducer.combat,
        ),
        restored,
        prototype_begin,
        system=True,
    )
    assert replayed == restored and replay == spent

    project = state.resources.inventions[0]
    assert project.active_work is not None
    state = advance(reducer, state, project.active_work.due, "time-prototype")
    state, _ = apply_invention(
        runtime,
        state,
        SettleInventionWork(
            id="settle-prototype",
            actor_id="a",
            expected_revision=state.revision,
            project_id=project.id,
            work_id=project.active_work.id,
        ),
        system=True,
    )
    assert state.resources.inventions[0].phase == "testing"
    state, _ = complete_phase(reducer, runtime, state, "testing", "testing")
    assert state.resources.inventions[0].phase == "production"
    state, product = complete_phase(reducer, runtime, state, "production", "production")
    project = state.resources.inventions[0]
    assert (project.phase, project.status, project.copies) == ("complete", "completed", 1)
    assert not product.behavior_available and not project.lots[0].behavior_available
    assert all(item.definition_id != "field-scanner" for item in state.resources.items)


def test_shortfalls_block_before_project_time_or_randomness() -> None:
    reducer, runtime, state = setup((1, 1, 1), facility_cost=1000)
    state = create(runtime, state)
    state, _ = complete_phase(reducer, runtime, state, "concept-design", "concept")
    before = state
    with pytest.raises(ValidationError, match="funding shortfall"):
        apply_invention(
            RulesContext(
                rng=RecordedDice(()),
                resources=reducer.resources,
                reviewer=reducer.reviewer,
                rules=reducer.rules,
                combat=reducer.combat,
            ),
            state,
            BeginInventionWork(
                id="unfunded",
                actor_id="a",
                expected_revision=state.revision,
                project_id="scanner-project",
                phase="prototype",
            ),
            system=True,
        )
    assert state == before and state.resources.inventions[0].active_work is None

    reducer, runtime, state = setup((1, 1, 1), facility_cost=0, material_quantity=10)
    state = create(runtime, state)
    state, _ = complete_phase(reducer, runtime, state, "concept-design", "material-concept")
    before = state
    with pytest.raises(ValidationError, match="material shortfall"):
        apply_invention(
            RulesContext(
                rng=RecordedDice(()),
                resources=reducer.resources,
                reviewer=reducer.reviewer,
                rules=reducer.rules,
                combat=reducer.combat,
            ),
            state,
            BeginInventionWork(
                id="unstocked",
                actor_id="a",
                expected_revision=state.revision,
                project_id="scanner-project",
                phase="prototype",
            ),
            system=True,
        )
    assert state == before and state.resources.inventions[0].active_work is None


def test_failed_testing_receipt_cannot_reroll_after_restart() -> None:
    reducer, runtime, state = setup((1, 1, 1, 1, 1, 1, 6, 6, 5))
    state = create(runtime, state)
    state, _ = complete_phase(reducer, runtime, state, "concept-design", "concept")
    state, _ = complete_phase(reducer, runtime, state, "prototype", "prototype")
    begin = BeginInventionWork(
        id="begin-test",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        phase="testing",
    )
    state, _ = apply_invention(runtime, state, begin, system=True)
    work = state.resources.inventions[0].active_work
    assert work is not None
    state = advance(reducer, state, work.due, "test-time")
    settle = SettleInventionWork(
        id="failed-test",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        work_id=work.id,
    )
    failed, outcome = apply_invention(runtime, state, settle, system=True)
    assert outcome.check and not outcome.check.outcome.succeeded
    restored = PlayState.model_validate_json(failed.model_dump_json())
    replayed, replay = apply_invention(
        RulesContext(
            rng=RecordedDice(()),
            resources=reducer.resources,
            reviewer=reducer.reviewer,
            rules=reducer.rules,
            combat=reducer.combat,
        ),
        restored,
        settle,
        system=True,
    )
    assert replayed == restored and replay == outcome


def test_active_invention_owns_the_shared_activity_clock() -> None:
    _, runtime, state = setup()
    state = create(runtime, state)
    state, _ = apply_invention(
        runtime,
        state,
        BeginInventionWork(
            id="begin-concept",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            phase="concept-design",
        ),
        system=True,
    )
    with pytest.raises(ConflictError, match="full-time invention"):
        synchronous(state, "a")
    restored = ResourceState.model_validate_json(state.resources.model_dump_json())
    assert restored.inventions[0].active_work == state.resources.inventions[0].active_work
