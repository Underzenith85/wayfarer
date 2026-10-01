"""Source-checked core size transitions change actual durable mechanics and HP."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import seed_play
from test_actions import Dice, campaign, world
from test_statistics import gurps_draft, profile_package
from trait_support import trait_compiler

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.traits.movement_forms import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.special_melee import actor_reaches, actor_size_modifier
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentCatalog,
    EquipmentProfile,
    Provenance,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, Pool, ResourceState
from wayfarer.engine.simulation.traits.size_forms import (
    SizeFormCommand,
    apply_size_form,
    body_geometry,
    effect_for,
    reduced_body_result,
    visible_effects,
)
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.size_forms import SizeFormService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize("delta, strength", [(4, 50), (-4, 10)])
async def test_durable_size_changes_each_second_and_returns_to_native_mechanics(
    tmp_path: Path,
    delta: int,
    strength: int,
) -> None:
    gear_package = replace(
        package(),
        definitions=package().definitions
        + tuple(
            RuleDefinition(
                "equipment:" + name,
                DefinitionKind.EQUIPMENT,
                name,
                package().sources[0].id,
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for name in ("test-bag", "test-cloak")
        ),
    )
    gear = EquipmentCatalog(
        profile_id=PROFILE,
        entries=tuple(
            EquipmentProfile(
                definition_id="equipment:" + name,
                provenance=Provenance(
                    source_id=package().sources[0].id,
                    edition="Fourth Edition, third printing (2008)",
                    pages=(85,),
                    errata="fixture",
                ),
                weight_millipounds=1000,
                price=0,
                technology_level=0,
                slot="body" if name == "test-cloak" else None,
                container_capacity_millipounds=10_000 if name == "test-bag" else None,
            )
            for name in ("test-bag", "test-cloak")
        ),
    )
    compiler = trait_compiler("size", PROFILE, gear_package, hooks=RUNTIME_HOOKS)
    compiler.policy = replace(
        compiler.policy,
        attribute_ceiling=100,
        allowed_equipment=frozenset(e.definition_id for e in gear.entries),
    )
    identifier = "advantage:growth" if delta > 0 else "advantage:shrinking"
    draft = gurps_draft(Purchase(definition_id=identifier, amount=4), st_level=strength)
    compiled = compiler.compile(draft).build
    assert compiled is not None
    reviewer = PowerReviewer(compiler, PowerPolicy(id="size", version=1), frozenset({"gm"}))
    reducer = ActionEngine(
        reviewer,
        ResourceEngine(
            world(),
            RulesCatalog(
                (
                    replace(
                        profile_package(PROFILE),
                        id="package:test-size",
                        definitions=profile_package(PROFILE).definitions + gear_package.definitions,
                    ),
                )
            ),
            compiler.rules,
            compiler.policy,
            tuple(e.inventory_spec() for e in gear.entries),
        ),
        ActionRules(
            id="size",
            version=1,
            combat=CombatRules(
                id="size",
                version=1,
                gurps_equipment=gear,
            ),
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "size.sqlite", 10), reducer, rng=Dice(5))
    initial = campaign(reducer)
    await seed_play(
        play,
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100_000),),
            items=(
                Item(id="bag", definition_id="equipment:test-bag", owner_id="a"),
                Item(
                    id="worn",
                    definition_id="equipment:test-cloak",
                    owner_id="a",
                    equipped=True,
                    ready=True,
                ),
                Item(
                    id="packed",
                    definition_id="equipment:test-cloak",
                    owner_id="a",
                    container_id="bag",
                ),
            ),
            pools=(
                Pool(id="hp:a", current=strength - 1, maximum=strength),
                Pool(id="fp:a", current=10, maximum=10),
            ),
        ),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft), aware_of=("b",)),),
    )
    service = SizeFormService(play)
    cid = initial["id"]
    command = SizeFormCommand(
        id="size-start", actor_id="a", expected_revision=0, kind="start", target_delta=delta
    )
    with pytest.raises(NotFoundError):
        await service.execute(cid, command, principal_id="b")
    started = await service.execute(cid, command, principal_id="a")
    assert started.current_delta == 0 and started.changing
    state = play._load(await play.store.read(cid))
    assert play.engine.resources.carried_weight(state.resources, "a") == (0 if delta < 0 else 3000)
    assert all(i.owner_id == "a" for i in state.resources.items)
    if delta < 0:
        assert (
            next(i for i in state.resources.items if i.id == "bag").world_ground_location_id
            == "dock"
        )
        assert not any(i.equipped or i.ready for i in state.resources.items)
    else:
        assert next(i for i in state.resources.items if i.id == "worn").ready
    assert await service.execute(cid, command, principal_id="a") == started
    with pytest.raises(ConflictError):
        await service.execute(cid, command.model_copy(update={"id": "stale"}), principal_id="a")
    for second in range(1, 5):
        state = play._load(await play.store.read(cid))
        await play.execute(
            cid,
            Wait(
                id="second-" + str(second), actor_id="a", expected_revision=state.revision, ticks=1
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        expected_delta = second if delta > 0 else -second
        assert actor_size_modifier(play.rules_context, state, "a") == expected_delta
        effect = effect_for(state.resources, "a")
        assert effect is not None and effect.current_delta == expected_delta
        assert effect.changing == (second < 4)
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    geometry = body_geometry(state.resources, "a")
    assert geometry.size_modifier == delta
    assert len(visible_effects(state.resources, state.world, "a")) == 1
    assert visible_effects(state.resources, state.world, "b") == ()
    if delta < 0:
        assert geometry.weight_fraction == Fraction(1, 100)
        assert geometry.table_dimension_yards == Fraction(1, 2)
        assert (hp.maximum, hp.current) == (2, 2)
        assert movement(play.rules_context, state, "a") == 1
        assert actor_reaches(play.rules_context, state, "a", (0, 1, 2)) == (0,)
        assert reduced_body_result(state.resources, "a", 7) == 1
    else:
        assert geometry.table_dimension_yards == 10
        assert geometry.weight_fraction is None
        assert (hp.maximum, hp.current) == (50, 50)
        assert movement(play.rules_context, state, "a") == 5
        assert actor_reaches(play.rules_context, state, "a", (1,)) == (1, 2, 3, 4)
    returning = await service.execute(
        cid,
        SizeFormCommand(
            id="return",
            actor_id="a",
            expected_revision=state.revision,
            kind="start",
            target_delta=0,
        ),
        principal_id="a",
    )
    assert returning.changing and returning.current_delta == delta
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="return-time", actor_id="a", expected_revision=state.revision, ticks=4),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert (hp.maximum, hp.current) == (strength, strength)
    assert actor_size_modifier(play.rules_context, state, "a") == 0
    if delta < 0:
        assert play.engine.resources.carried_weight(state.resources, "a") == 0
        for item_id in ("bag", "worn"):
            state = play._load(await play.store.read(cid))
            await service.retrieve(
                cid,
                {
                    "id": "retrieve-" + item_id,
                    "actor_id": "a",
                    "expected_revision": state.revision,
                    "kind": "retrieve",
                    "item_id": item_id,
                },
                principal_id="a",
            )
        state = play._load(await play.store.read(cid))
        assert play.engine.resources.carried_weight(state.resources, "a") == 3000
        assert not any(i.ready or i.equipped for i in state.resources.items)
    assert await service.execute(cid, command, principal_id="a") == started


def test_growth_requires_actual_purchased_strength_and_tiny_hp_is_explicitly_unsupported() -> None:
    compiler = trait_compiler("size", PROFILE, package(), hooks=RUNTIME_HOOKS)
    for identifier, target, message in (
        ("advantage:growth", 4, "purchased ST"),
        ("advantage:shrinking", -8, "fractional-health"),
    ):
        compiled = compiler.compile(gurps_draft(Purchase(definition_id=identifier, amount=8))).build
        assert compiled is not None
        with pytest.raises(ValidationError, match=message):
            apply_size_form(
                ResourceState(pools=(Pool(id="hp:a", current=10, maximum=10),)),
                SizeFormCommand(
                    id="size", actor_id="a", expected_revision=0, kind="start", target_delta=target
                ),
                compiled,
                authorized_actor_id="a",
                system=True,
            )


def test_exact_wound_fraction_survives_interruption_and_reversion() -> None:
    from wayfarer.engine.simulation.traits.size_forms import checkpoint

    compiler = trait_compiler("size", PROFILE, package(), hooks=RUNTIME_HOOKS)
    compiled = compiler.compile(
        gurps_draft(Purchase(definition_id="advantage:shrinking", amount=4))
    ).build
    assert compiled is not None
    state = ResourceState(pools=(Pool(id="hp:a", current=9, maximum=10),))
    state, _ = apply_size_form(
        state,
        SizeFormCommand(
            id="start", actor_id="a", expected_revision=0, kind="start", target_delta=-4
        ),
        compiled,
        authorized_actor_id="a",
        system=True,
    )
    state = checkpoint(state.model_copy(update={"game_time": 1}))
    assert (state.pools[0].maximum, state.pools[0].current) == (7, 6)
    state, stopped = apply_size_form(
        state,
        SizeFormCommand(id="interrupt", actor_id="a", expected_revision=1, kind="interrupt"),
        compiled,
        authorized_actor_id="a",
        system=True,
    )
    assert stopped.current_delta == -1 and not stopped.changing
    state = checkpoint(state.model_copy(update={"game_time": 100}))
    assert effect_for(state, "a") == stopped
    state, _ = apply_size_form(
        state,
        SizeFormCommand(
            id="return", actor_id="a", expected_revision=2, kind="start", target_delta=0
        ),
        compiled,
        authorized_actor_id="a",
        system=True,
    )
    state = checkpoint(state.model_copy(update={"game_time": 101}))
    assert (state.pools[0].maximum, state.pools[0].current) == (10, 9)
    native_effect = effect_for(state, "a")
    assert native_effect is not None and native_effect.current_delta == 0


async def test_shrunk_combat_body_drops_gear_at_actual_position_and_reduces_actual_punch_damage(
    tmp_path: Path,
) -> None:
    from test_gurps_melee import setup
    from test_unarmed import action, defend, wait

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.resources import Equip
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    cid, play = await setup(
        tmp_path,
        PROFILE,
        human=True,
        unarmed_fixture=True,
        allow_supernatural=True,
        extra_definitions=package().definitions,
        extra_purchases=(Purchase(definition_id="advantage:shrinking", amount=1),),
        trait_runtime_hooks=RUNTIME_HOOKS,
    )
    service = SizeFormService(play)
    state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        SizeFormCommand(
            id="shrink",
            actor_id="a",
            expected_revision=state.revision,
            kind="start",
            target_delta=-1,
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    sword = next(i for i in state.resources.items if i.id == "sword-a")
    assert sword.owner_id == "a" and not sword.equipped and not sword.ready
    assert sword.ground is not None and (sword.ground.x, sword.ground.y) == (0, 0)
    assert state.encounters[0].participants[0].ready_item_ids == ()
    assert state.encounters[0].participants[0].hand_bindings == ()
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="size-ready",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="ready",
        ),
        principal_id="a",
    )
    await wait(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[0].movement_allowance == 3
    with pytest.raises(ValidationError, match="authoritative retrieval"):
        play.engine.resources.apply(
            state.resources,
            Equip(
                id="equip",
                actor_id="a",
                expected_revision=state.resources.revision,
                item_id="sword-a",
            ),
        )
    play.rng = RecordedDice((3, 3, 3, 6))
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    await defend(cid, play)
    state = play._load(await play.store.read(cid))
    target_hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert target_hp.current == 8  # ST10 thrust 1d-2, punch -1 => 3; B85 scales 3/4 down to2.
    assert next(i for i in state.resources.items if i.id == "sword-a").ground == sword.ground


def test_printed_shrinking_twelve_example_changes_committed_body_geometry() -> None:
    from wayfarer.engine.simulation.traits.size_forms import checkpoint

    compiler = trait_compiler("size", PROFILE, package(), hooks=RUNTIME_HOOKS)
    compiler.policy = replace(compiler.policy, attribute_ceiling=150)
    compiled = compiler.compile(
        gurps_draft(Purchase(definition_id="advantage:shrinking", amount=12), st_level=100)
    ).build
    assert compiled is not None
    state = ResourceState(pools=(Pool(id="hp:a", current=100, maximum=100),))
    state, _ = apply_size_form(
        state,
        SizeFormCommand(
            id="twelve", actor_id="a", expected_revision=0, kind="start", target_delta=-12
        ),
        compiled,
        authorized_actor_id="a",
        system=True,
    )
    state = checkpoint(state.model_copy(update={"game_time": 12}))
    geometry = body_geometry(state, "a")
    assert geometry.size_modifier == -12
    assert geometry.height_fraction == Fraction(1, 100)
    assert geometry.weight_fraction == Fraction(1, 1_000_000)
    assert state.pools[0].maximum == 1 and state.pools[0].current == 1
    assert reduced_body_result(state, "a", 5) == 0
