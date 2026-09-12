"""Executable magic-item lifecycle expectations from Campaigns B480-482 (#526)."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_actions import campaign
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.gurps_magic import definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.enchanting import (
    EnchantingRules,
    EnchantmentMaterial,
    EnchantmentRecipe,
    EnergyContribution,
    MagicItemOffer,
)
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    BeginEnchanting,
    CreateEnchantment,
    InterruptEnchanting,
    SettleEnchanting,
    apply_enchantment,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellExecutionContext,
    approved_context,
    reduce_spell,
)
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Advance, EquipmentSpec, Item, Owner, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def setup(
    tmp_path: Path,
    *,
    method: Literal["quick-and-dirty", "slow-and-sure"] = "slow-and-sure",
    runtime_family: str | None = "spell",
) -> tuple[ActionEngine, RulesContext, PlayState]:
    enchantment = enchantment_package()
    all_definitions = {d.id: d for d in (*definitions(2), *enchantment.definitions)}
    for key in ("sword", "workshop", "silver"):
        all_definitions["equipment:" + key] = RuleDefinition(
            "equipment:" + key,
            DefinitionKind.EQUIPMENT,
            key,
            "sjg:basic-set-characters-4e-2004",
            0,
            ImplementationStatus.IMPLEMENTED,
        )
    package = profile_package("gurps-basic-set-4e-2004", *all_definitions.values())
    package = replace(
        package,
        sources=package.sources
        + tuple(s for s in enchantment.sources if s.id not in {x.id for x in package.sources}),
    )
    base = profile_compiler("gurps-basic-set-4e-2004", package=package)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        base.rules,
        replace(
            base.policy,
            point_budget=200,
            allow_supernatural=True,
            allowed_equipment=frozenset(
                {"equipment:sword", "equipment:workshop", "equipment:silver"}
            ),
        ),
        statistics_profile="gurps-basic-set-4e-2004",
    )
    world = World(
        entities=(
            Entity("forge", EntityKind.LOCATION, "Forge"),
            Entity("a", EntityKind.ACTOR, "Lead", "forge"),
            Entity("b", EntityKind.ACTOR, "Assistant", "forge"),
        )
    )
    resources = ResourceState(
        owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
        items=(
            Item(id="blade", definition_id="equipment:sword", owner_id="a"),
            Item(id="forge-tools", definition_id="equipment:workshop", owner_id="a"),
            Item(id="silver", definition_id="equipment:silver", owner_id="a", quantity=3),
        ),
    )
    resource_engine = ResourceEngine(
        world,
        RulesCatalog((package,)),
        compiler.rules,
        compiler.policy,
        (
            EquipmentSpec(definition_id="equipment:sword", unit_weight=5, stackable=False),
            EquipmentSpec(definition_id="equipment:workshop", unit_weight=20, stackable=False),
            EquipmentSpec(definition_id="equipment:silver", unit_weight=1),
        ),
    )
    recipe = EnchantmentRecipe(
        id="light-blade",
        spell_id="spell:light",
        effect_id="effect:light",
        method=method,
        energy_required=4,
        target_definition_ids=("equipment:sword",),
        workspace_definition_id="equipment:workshop",
        materials=(EnchantmentMaterial(definition_id="equipment:silver", quantity=2),),
        runtime_spell_id="light",
        runtime_family=runtime_family,
        maximum_charges=2,
    )
    action_rules = ActionRules(
        id="magic",
        version=1,
        enchanting=EnchantingRules(id="enchanting", version=1, recipes=(recipe,)),
        spells=SpellRules(
            id="spells",
            version=1,
            channels=(
                SpellChannel(
                    id="item-light",
                    actor_id="a",
                    target_id="a",
                    location_id="forge",
                    spell_id="light",
                    magic_item_id="blade",
                ),
            ),
        ),
    )
    reducer = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="power", version=1), frozenset({"gm"})),
        resource_engine,
        action_rules,
    )
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=2),
        Purchase(definition_id="spell:enchant", amount=4),
        Purchase(definition_id="spell:light", amount=4),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 16}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    service = PlayService(AsyncSQLiteStore(tmp_path / "unused.sqlite", 10), reducer)
    state = service.initial_state(
        campaign(reducer),
        world,
        resources,
        (
            ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=draft)),
        ),
    )
    runtime = RulesContext(
        rng=RecordedDice((3, 3, 3)),
        resources=resource_engine,
        reviewer=reducer.reviewer,
        rules=action_rules,
        combat=None,
    )
    reducer.validate(state)
    return reducer, runtime, state


def advance(reducer: ActionEngine, state: PlayState, to: int, identifier: str) -> PlayState:
    resources = reducer.resources.apply(
        state.resources,
        Advance(id=identifier, actor_id="a", expected_revision=state.revision, to=to),
        system=True,
        rng=RecordedDice(()),
    )
    return state.model_copy(update={"revision": resources.revision, "resources": resources})


def create(runtime: RulesContext, state: PlayState) -> tuple[PlayState, CreateEnchantment]:
    command = CreateEnchantment(
        id="create",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        recipe_id="light-blade",
        target_item_id="blade",
        enchanter_ids=("a", "b"),
    )
    state, _ = apply_enchantment(runtime, state, command, system=True)
    return state, command


def test_slow_project_interrupt_resume_restart_and_compile_item(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state, create_command = create(runtime, state)
    assert next(i for i in state.resources.items if i.id == "silver").quantity == 1
    restarted = PlayState.model_validate_json(state.model_dump_json())
    assert apply_enchantment(runtime, restarted, create_command, system=True)[0] == restarted

    begin = BeginEnchanting(
        id="begin", actor_id="a", expected_revision=state.revision, project_id="project"
    )
    state, _ = apply_enchantment(runtime, state, begin, system=True)
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 2 * 8 * 60 * 60
    with pytest.raises(ConflictError, match="enchanting work"):
        synchronous(state, "b")
    with pytest.raises(ConflictError, match="deadline"):
        apply_enchantment(
            runtime,
            state,
            SettleEnchanting(
                id="early",
                actor_id="a",
                expected_revision=state.revision,
                project_id="project",
                work_id=work.id,
            ),
            system=True,
        )
    state = advance(reducer, state, 8 * 60 * 60, "day-one")
    state, interrupted = apply_enchantment(
        runtime,
        state,
        InterruptEnchanting(
            id="interrupt", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    assert interrupted.energy_completed == 2
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="resume", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due - work.start == 3 * 8 * 60 * 60
    state = advance(reducer, state, work.due, "finish-time")
    settle = SettleEnchanting(
        id="settle",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id=work.id,
    )
    state, outcome = apply_enchantment(runtime, state, settle, system=True)
    item = next(i for i in state.resources.items if i.id == "blade")
    assert outcome.status == "completed"
    assert item.enchantments[0].project_id == "project"
    assert (item.enchantments[0].charges, item.enchantments[0].runtime_family) == (2, "spell")
    restored = PlayState.model_validate_json(state.model_dump_json())
    assert apply_enchantment(runtime, restored, settle, system=True) == (restored, outcome)


def test_quick_energy_is_preflighted_spent_once_and_bad_target_is_early(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path, method="quick-and-dirty")
    bad = next(i for i in state.resources.items if i.id == "blade").model_copy(
        update={"definition_id": "equipment:silver"}
    )
    invalid = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"items": tuple(bad if i.id == bad.id else i for i in state.resources.items)}
            )
        }
    )
    with pytest.raises(ValidationError, match="not suitable"):
        create(runtime, invalid)
    assert invalid.resources.game_time == 0 and invalid.resources.receipts == ()

    state, _ = create(runtime, state)
    before = state
    with pytest.raises(ValidationError, match="explicit contribution"):
        apply_enchantment(
            runtime,
            state,
            BeginEnchanting(
                id="bad-energy",
                actor_id="a",
                expected_revision=state.revision,
                project_id="project",
                contributions=(EnergyContribution(actor_id="a", fp=1),),
            ),
            system=True,
        )
    assert state == before
    begin = BeginEnchanting(
        id="quick",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        contributions=(
            EnergyContribution(actor_id="a", fp=2),
            EnergyContribution(actor_id="b", fp=2),
        ),
    )
    state, _ = apply_enchantment(runtime, state, begin, system=True)
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due - work.start == 3600
    state = advance(reducer, state, work.due, "quick-time")
    state, outcome = apply_enchantment(
        runtime,
        state,
        SettleEnchanting(
            id="quick-settle",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
            work_id=work.id,
        ),
        system=True,
    )
    assert outcome.status == "completed"
    assert [p.current for p in state.resources.pools if p.id in ("fp:a", "fp:b")] == [8, 8]
    offer = MagicItemOffer(
        id="forge-price",
        recipe_id="light-blade",
        price=137,
        availability="rare",
        market_id="market:forge",
    )
    assert (offer.price, offer.availability) == (137, "rare")
    assert runtime.rules.enchanting is not None and runtime.rules.enchanting.offers == ()


def test_item_activation_blocks_missing_family_and_spends_one_charge(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state, _ = create(runtime, state)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    state = advance(reducer, state, work.due, "time")
    state, _ = apply_enchantment(
        runtime,
        state,
        SettleEnchanting(
            id="settle",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
            work_id=work.id,
        ),
        system=True,
    )
    blade = next(i for i in state.resources.items if i.id == "blade").model_copy(
        update={"equipped": True, "ready": True}
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(blade if i.id == blade.id else i for i in state.resources.items)
                }
            )
        }
    )
    command = SpellCommand(
        id="use-item",
        actor_id="a",
        expected_revision=state.revision,
        kind="start",
        spell_id="light",
        channel_id="item-light",
        cast_id="item-cast",
    )
    assert approved_context(runtime, state, command).skill == blade.enchantments[0].power
    state, result = reduce_spell(state, command, SpellExecutionContext(runtime))
    assert result.outcome == "casting"
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].charges == 1
    replay, replay_result = reduce_spell(state, command, SpellExecutionContext(runtime))
    assert replay_result == result
    assert next(i for i in replay.resources.items if i.id == "blade").enchantments[0].charges == 1

    enchanting = runtime.rules.enchanting
    assert enchanting is not None
    blocked_runtime = replace(
        runtime,
        rules=runtime.rules.model_copy(
            update={
                "enchanting": enchanting.model_copy(
                    update={
                        "recipes": (
                            enchanting.recipes[0].model_copy(
                                update={"runtime_family": None, "runtime_spell_id": None}
                            ),
                        )
                    }
                )
            }
        ),
    )
    blocked_instance = blade.enchantments[0].model_copy(update={"runtime_family": None})
    blocked_blade = blade.model_copy(update={"enchantments": (blocked_instance,)})
    blocked = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        blocked_blade if i.id == blocked_blade.id else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError, match="no executable runtime family"):
        approved_context(
            blocked_runtime,
            blocked,
            command.model_copy(update={"id": "blocked", "expected_revision": blocked.revision}),
        )
