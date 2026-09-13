"""Independent ordinary-invention expectations derived from Campaigns B473-475."""

from dataclasses import replace

import pytest
from test_actions import engine, seed, world
from test_mundane_traits import combined_package
from test_statistics import BASIC, gurps_draft

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.projects.invention_transitions import (
    BeginInventionWork,
    CreateInvention,
    InventionOutcome,
    PauseInventionWork,
    ResumeInventionWork,
    SettleInventionWork,
    apply_invention,
    gadget_operation_allowed,
)
from wayfarer.engine.simulation.projects.inventions import (
    GadgetContext,
    InventionBlueprint,
    InventionPhase,
    InventionRules,
    MaterialRequirement,
    StageRequirement,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Advance, EquipmentSpec, Pool, ResourceState
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


def gadget_rules(method: str = "quick-gadgeteer", *, activity: str = "invention") -> InventionRules:
    blueprint = (
        rules(facility_cost=999)
        .blueprints[0]
        .model_copy(
            update={
                "method": method,
                "activity": activity,
                "subject_definition_id": None if activity == "invention" else "tool",
                "native_tl": 9,
                "inventor_tl": 8,
                "campaign_tl": 8,
                "retail_price": 100,
                "gadget_context": GadgetContext(powered=True),
                "invention_skill_id": "attribute:iq",
                "related_skill_ids": (),
                "operation_skill_id": "attribute:iq",
            }
        )
    )
    return InventionRules(
        id="gadget-inventions",
        version=1,
        blueprints=(blueprint,),
        permitted_methods=("ordinary", "gadgeteer", "quick-gadgeteer"),
    )


def gadget_setup(
    level: int = 2, *, method: str = "quick-gadgeteer", activity: str = "invention"
) -> tuple[ActionEngine, RulesContext, PlayState]:
    base = engine()
    package = combined_package()
    equipment = tuple(
        RuleDefinition(
            id=identifier,
            kind=DefinitionKind.EQUIPMENT,
            name=identifier.title(),
            source_id=package.sources[0].id,
            point_cost=0,
            status=ImplementationStatus.IMPLEMENTED,
        )
        for identifier in ("potion", "sword", "tool")
    )
    package = replace(package, definitions=package.definitions + equipment)
    policy = CampaignPolicy(
        id="gadget-policy",
        version=1,
        point_budget=150,
        disadvantage_limit=200,
        attribute_ceiling=20,
        skill_ceiling=20,
        permitted_sources=frozenset(source.id for source in package.sources),
        allowed_equipment=frozenset({"potion", "sword", "tool"}),
    )
    campaign_rules = CampaignRules(
        edition=package.edition,
        packages=(PackagePin(package.id, package.version, package.digest),),
        policy_id=policy.id,
        policy_version=policy.version,
    )
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        campaign_rules,
        policy,
        statistics_profile=BASIC,
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    reviewer = PowerReviewer(
        compiler,
        PowerPolicy(id="gadget-power", version=1, automatic_approval=True),
    )
    action_rules = ActionRules(
        id="gadget-actions",
        version=1,
        inventions=gadget_rules(method, activity=activity),
    )
    resources = ResourceEngine(
        world(),
        catalog,
        campaign_rules,
        policy,
        (
            EquipmentSpec(definition_id="potion", unit_weight=1),
            EquipmentSpec(definition_id="sword", unit_weight=5, stackable=False, slot="hand"),
            EquipmentSpec(definition_id="tool", unit_weight=1, stackable=False, slot="eye"),
        ),
    )
    reducer = ActionEngine(reviewer, resources, action_rules)
    state = seed(base)
    proposal = CharacterProposal(
        draft=gurps_draft(Purchase(definition_id="trait:advantage:gadgeteer", amount=level))
    )
    approval = reviewer.approve(proposal, campaign_id="c", actor_id="a", revision=0)
    state = state.model_copy(
        update={
            "configuration_digest": reducer.digest,
            "actors": (
                state.actors[0].model_copy(update={"proposal": proposal, "approval": approval}),
            ),
            "approvals": (approval,),
            "resources": state.resources.model_copy(
                update={
                    "pools": state.resources.pools
                    + (Pool(id="money:a", current=1_000_000, maximum=1_000_000),)
                }
            ),
        }
    )
    runtime = RulesContext(
        rng=RecordedDice(()),
        resources=reducer.resources,
        reviewer=reviewer,
        rules=reducer.rules,
        combat=reducer.combat,
    )
    return reducer, runtime, state


def dice(runtime: RulesContext, values: tuple[int, ...]) -> RulesContext:
    return replace(runtime, rng=RecordedDice(values))


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


def test_compiled_capability_and_campaign_permission_gate_gadget_methods() -> None:
    _, runtime, state = gadget_setup(level=1, method="quick-gadgeteer")
    with pytest.raises(ValidationError, match="Gadgeteer capability"):
        create(runtime, state)

    with pytest.raises(ValueError, match="explicit campaign permission"):
        InventionRules(
            id="forbidden-gadgets",
            version=1,
            blueprints=gadget_rules().blueprints,
        )


def test_quick_schedule_defects_and_improvised_parts_replay_exactly() -> None:
    reducer, runtime, state = gadget_setup()
    state = create(runtime, state)

    begin_concept = BeginInventionWork(
        id="quick-concept",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        phase="concept-design",
    )
    state, concept = apply_invention(dice(runtime, (4,)), state, begin_concept, system=True)
    assert concept.schedule_kind == "quick-random"
    assert concept.schedule_dice == (4,) and concept.due == 240
    state = advance(reducer, state, 240, "quick-concept-time")
    project = state.resources.inventions[0]
    assert project.active_work is not None
    state, _ = apply_invention(
        dice(runtime, (3, 3, 3)),
        state,
        SettleInventionWork(
            id="quick-concept-settle",
            actor_id="a",
            expected_revision=state.revision,
            project_id=project.id,
            work_id=project.active_work.id,
        ),
        system=True,
    )

    begin_prototype = BeginInventionWork(
        id="quick-prototype",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        phase="prototype",
    )
    before_materials = next(item for item in state.resources.items if item.id == "potions").quantity
    state, started = apply_invention(dice(runtime, (2, 3)), state, begin_prototype, system=True)
    assert started.schedule_dice == (2, 3) and started.due == 540
    assert started.money_spent == 1503  # B475 facility table /100 + adjusted retail /100.
    assert started.materials_spent[0].quantity == 2
    assert next(item for item in state.resources.items if item.id == "potions").quantity == (
        before_materials - 2
    )
    restored = PlayState.model_validate_json(state.model_dump_json())
    replayed, replay = apply_invention(dice(runtime, ()), restored, begin_prototype, system=True)
    assert replayed == restored and replay == started

    state = advance(reducer, state, 540, "quick-prototype-time")
    project = state.resources.inventions[0]
    assert project.active_work is not None
    settle = SettleInventionWork(
        id="quick-prototype-settle",
        actor_id="a",
        expected_revision=state.revision,
        project_id=project.id,
        work_id=project.active_work.id,
    )
    # Prototype 9 succeeds by one; bug-count 4 gives two bugs, selected by 9 and 13.
    state, outcome = apply_invention(
        dice(runtime, (3, 3, 3, 4, 3, 3, 3, 4, 4, 5)), state, settle, system=True
    )
    assert tuple(defect.kind for defect in outcome.defects) == ("power-hungry", "unreliable")
    assert tuple(defect.table_dice for defect in outcome.defects) == ((3, 3, 3), (4, 4, 5))
    restored = PlayState.model_validate_json(state.model_dump_json())
    replayed, replay = apply_invention(dice(runtime, ()), restored, settle, system=True)
    assert replayed == restored and replay == outcome

    state, testing = apply_invention(
        runtime,
        state,
        BeginInventionWork(
            id="quick-testing",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            phase="testing",
        ),
        system=True,
    )
    assert testing.due == 570
    state = advance(reducer, state, 570, "quick-testing-time")
    work = state.resources.inventions[0].active_work
    assert work is not None
    settle_testing = SettleInventionWork(
        id="quick-testing-settle",
        actor_id="a",
        expected_revision=state.revision,
        project_id="scanner-project",
        work_id=work.id,
    )
    state, discovery = apply_invention(dice(runtime, (4, 4, 4)), state, settle_testing, system=True)
    assert discovery.status == "gadget-bug-discovered"
    assert discovery.defects[0].discovered_at == 570
    restored = PlayState.model_validate_json(state.model_dump_json())
    replayed, replay = apply_invention(dice(runtime, ()), restored, settle_testing, system=True)
    assert replayed == restored and replay == discovery


def test_gadget_work_pauses_for_adventure_time_and_non_gadget_access_is_bounded() -> None:
    reducer, runtime, state = gadget_setup(method="gadgeteer")
    state = create(runtime, state)
    state, _ = apply_invention(
        runtime,
        state,
        BeginInventionWork(
            id="begin-gadget-work",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            phase="concept-design",
        ),
        system=True,
    )
    work = state.resources.inventions[0].active_work
    assert work is not None and work.schedule_kind == "gadgeteer-interruptible"
    state = advance(reducer, state, 4, "partial-work")
    state, paused = apply_invention(
        runtime,
        state,
        PauseInventionWork(
            id="pause-gadget-work",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            work_id=work.id,
        ),
        system=True,
    )
    assert paused.status == "work-paused"
    assert state.resources.inventions[0].active_work is not None
    assert state.resources.inventions[0].active_work.remaining_seconds == 6
    state = advance(reducer, state, 100, "adventure-time")
    state, resumed = apply_invention(
        runtime,
        state,
        ResumeInventionWork(
            id="resume-gadget-work",
            actor_id="a",
            expected_revision=state.revision,
            project_id="scanner-project",
            work_id=work.id,
        ),
        system=True,
    )
    assert resumed.due == 106

    _, ordinary_runtime, ordinary_state = setup()
    blueprint = gadget_rules("gadgeteer").blueprints[0]
    assert gadget_operation_allowed(ordinary_runtime, ordinary_state, "a", blueprint, "use")
    assert gadget_operation_allowed(ordinary_runtime, ordinary_state, "a", blueprint, "repair")
    assert not gadget_operation_allowed(
        ordinary_runtime, ordinary_state, "a", blueprint, "reproduce"
    )
    assert not gadget_operation_allowed(ordinary_runtime, ordinary_state, "a", blueprint, "invent")

    analysis_reducer, analysis_runtime, analysis_state = gadget_setup(activity="analysis")
    analysis_state = create(analysis_runtime, analysis_state)
    analysis_state, analysis = apply_invention(
        dice(analysis_runtime, (5,)),
        analysis_state,
        BeginInventionWork(
            id="analyze-encountered-gadget",
            actor_id="a",
            expected_revision=analysis_state.revision,
            project_id="scanner-project",
            phase="concept-design",
        ),
        system=True,
    )
    assert analysis.schedule_dice == (5,) and analysis.due == 300
    analysis_state = advance(analysis_reducer, analysis_state, 300, "analysis-time")
    active = analysis_state.resources.inventions[0].active_work
    assert active is not None
    analysis_state, analyzed = apply_invention(
        dice(analysis_runtime, (3, 3, 3)),
        analysis_state,
        SettleInventionWork(
            id="settle-analysis",
            actor_id="a",
            expected_revision=analysis_state.revision,
            project_id="scanner-project",
            work_id=active.id,
        ),
        system=True,
    )
    assert analyzed.status == "analysis-complete"
    assert analysis_state.resources.inventions[0].status == "completed"
