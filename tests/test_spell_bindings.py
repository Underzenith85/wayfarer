"""B66/B235-250 training expectations and approved-build transaction evidence.

Hand-entered numeric examples under the model-knowledge implementation policy;
exact-printing source certification remains in the catalog audit.
"""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_abilities import resources, world
from test_actions import campaign
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.gurps_magic import definitions
from wayfarer.engine.rules.magic.protocols import MagicItemBinding
from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog, EquipmentProfile
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.bindings import BackfireAlternative, SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


def compiler() -> CharacterCompiler:
    package = profile_package(PROFILE, *definitions(), projectile_definition())
    base = profile_compiler(PROFILE, package=package)
    return CharacterCompiler(
        RulesCatalog((package,)),
        base.rules,
        replace(base.policy, allow_supernatural=True),
        statistics_profile=PROFILE,
    )


def draft(*, magery: int = 2, points: int = 4) -> CharacterDraft:
    value = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        *((Purchase(definition_id="trait:magery", amount=magery),) if magery else ()),
        *(
            Purchase(definition_id="spell:" + key, amount=points)
            for key in (
                "light",
                "foolishness",
                "daze",
                "ignite-fire",
                "create-fire",
                "shape-fire",
                "fireball",
            )
        ),
    )

    return value.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:iq" else p
                for p in value.purchases
            )
        }
    )


async def setup(
    tmp_path: Path,
    *,
    combat: bool = False,
    backend: str = "sqlite",
    caster_hp: int = 10,
    execution_version: Literal[1, 2] = 1,
    alternatives: tuple[BackfireAlternative, ...] = (),
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal",
    reserve: bool = False,
    equipment: tuple[EquipmentProfile, ...] = (),
) -> tuple[str, PlayService]:
    fixture_world, fixture_resources = world(), resources()
    if reserve:
        from wayfarer.engine.simulation.resources import Owner
        from wayfarer.engine.world import Entity, EntityKind

        fixture_world = replace(
            fixture_world,
            entities=fixture_world.entities + (Entity("c", EntityKind.ACTOR, "Reserve", "room"),),
        )
        fixture_resources = fixture_resources.model_copy(
            update={
                "owners": fixture_resources.owners + (Owner(actor_id="c", capacity=100),),
                "pools": fixture_resources.pools
                + (fixture_resources.pools[0].model_copy(update={"id": "hp:c"}),),
            }
        )
    compiled = compiler()
    from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
    from wayfarer.engine.rules.types.object import ObjectCondition
    from wayfarer.engine.simulation.resources import Item

    package = profile_package(
        PROFILE,
        *definitions(),
        projectile_definition(),
        *(
            RuleDefinition(
                e.definition_id,
                DefinitionKind.EQUIPMENT,
                e.definition_id,
                "sjg:basic-set-characters-4e-2004",
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for e in equipment
        ),
    )
    if equipment:
        base = profile_compiler(PROFILE, package=package)
        compiled = CharacterCompiler(
            RulesCatalog((package,)),
            base.rules,
            replace(
                base.policy,
                allow_supernatural=True,
                allowed_equipment=frozenset(e.definition_id for e in equipment),
            ),
            statistics_profile=PROFILE,
        )
    fixture_resources = fixture_resources.model_copy(
        update={
            "items": tuple(
                Item(
                    id=f"target-{n}",
                    definition_id=e.definition_id,
                    owner_id="b",
                    equipped=True,
                    ready=True,
                    condition=ObjectCondition(hp=e.durability.hp) if e.durability else None,
                )
                for n, e in enumerate(equipment)
            )
        }
    )
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="power", version=1), frozenset({"gm"})),
        ResourceEngine(
            fixture_world,
            RulesCatalog((package,)),
            compiled.rules,
            compiled.policy,
            tuple(e.inventory_spec() for e in equipment),
        ),
        ActionRules(
            id="spells",
            version=1,
            combat=CombatRules(
                id="arena",
                version=1,
                battlefields=(Battlefield(id="room", location_id="room", width=5, height=5),),
                gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=equipment),
            )
            if combat
            else None,
            spells=SpellRules(
                id="spell-rules",
                version=1,
                execution_version=execution_version,
                backfire_alternatives=alternatives,
                channels=tuple(
                    SpellChannel(
                        id=spell,
                        actor_id="a",
                        target_id="b",
                        location_id="room",
                        spell_id=spell,
                        mana=mana,
                    )
                    for spell in ("light", "daze", "fireball", "create-fire")
                ),
            ),
        ),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "spells.sqlite", 10), engine, rng=RecordedDice([])
    )
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        play = PlayService(AsyncPostgresStore(url, 10), engine, rng=RecordedDice([]))
    initial = campaign(engine)
    initial_state = play.initial_state(
        initial,
        fixture_world,
        fixture_resources,
        (
            ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft())),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),
        )
        + (
            (ActorSetup(actor_id="c", proposal=CharacterProposal(draft=gurps_draft())),)
            if reserve
            else ()
        ),
    )
    initial_state = initial_state.model_copy(
        update={
            "resources": initial_state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": caster_hp}) if p.id == "hp:a" else p
                        for p in initial_state.resources.pools
                    )
                }
            )
        }
    )
    initial["play_json"] = initial_state.model_dump_json()
    await play.store.insert(initial)
    return initial["id"], play


def command(revision: int = 0, kind: str = "start") -> SpellCommand:
    return SpellCommand.model_validate(
        dict(
            id=f"spell-{revision}",
            actor_id="a",
            expected_revision=revision,
            kind=kind,
            spell_id="light",
            channel_id="light",
            cast_id="cast",
        )
    )


def test_magery_costs_and_trained_prerequisites_use_effective_levels() -> None:
    result = compiler().compile(draft())
    assert result.build is not None, result.diagnostics
    assert result.spent == 93  # IQ12:40; Magery 0:5; Magery 2:20; seven skills:28.
    assert {int(v.value) for v in result.build.sheet.values if v.target.startswith("spell:")} == {
        14
    }
    # IQ12 + Magery1 - 2 for one point =11, below the prerequisite of12.
    result = compiler().compile(draft(magery=1, points=1))
    assert any(d.code == "skill.prerequisite" for d in result.diagnostics)
    result = compiler().compile(draft(magery=1, points=8))
    assert result.build is not None, result.diagnostics
    assert result.spent == 111


def test_no_default_magic_and_modified_catalog_rejected() -> None:
    result = compiler().compile(gurps_draft())
    assert result.build is not None
    assert not any(v.target.startswith("spell:") for v in result.build.sheet.values)
    entries = definitions()
    package = profile_package(PROFILE, replace(entries[0], point_cost=0), *entries[1:])
    with pytest.raises(ValidationError, match="exact Basic Set catalog"):
        profile_compiler(PROFILE, package=package)


async def test_magic_item_power_replaces_user_spell_skill(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
    from wayfarer.engine.simulation.magic.binding_context import approved_context as bind_context

    cid, play = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    context = bind_context(
        play.rules_context,
        state,
        command(),
        SpellEnvironment(
            target_id="b",
            mana="low",
            magic_item=MagicItemBinding(
                id="wand-light",
                item_id="wand",
                spell_id="light",
                power=20,
                power_reduction=2,
            ),
        ),
    )
    assert context.skill == 15
    assert context.item_power_reduction == 2


async def test_player_context_is_compiled_and_retries_are_durable(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    service = SpellService(play)
    before = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await service.execute(cid, command(), principal_id="b")
    with pytest.raises(AuthorizationError):
        await service.execute(
            cid, command().model_copy(update={"channel_id": "unknown"}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    bound = approved_context(play.rules_context, play._load(before), command())
    assert (bound.skill, bound.magery, bound.ht, bound.will) == (14, 2, 10, 12)
    assert (await service.execute(cid, command(), principal_id="a")).outcome == "casting"
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3])
    complete = command(2, "complete")
    results = await asyncio.gather(
        *(service.execute(cid, complete, principal_id="a") for _ in range(3))
    )
    assert all(r == results[0] for r in results)
    assert results[0].energy_spent == 1
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "spells.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    assert await SpellService(restarted).execute(cid, complete, principal_id="a") == results[0]
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_changing_spell_channels_requires_explicit_migration(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    rules = play.engine.rules
    assert rules.spells
    altered = rules.model_copy(update={"spells": rules.spells.model_copy(update={"version": 2})})
    changed = ActionEngine(play.engine.reviewer, play.engine.resources, altered)
    with pytest.raises(ValidationError, match="explicit migration"):
        changed.validate(play._load(await play.store.read(cid)))


async def test_daze_casting_seconds_combat_restrictions_and_injury_cancellation(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, combat=True)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="encounter",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="room",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=2, y=1)),
            ),
        ),
        authenticated_actor_id="gm",
    )
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "daze", "channel_id": "daze"})
    await service.execute(cid, start, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "b"
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="b1", actor_id="b", expected_revision=2, encounter_id="fight", maneuver="do_nothing"
        ),
        authenticated_actor_id="b",
    )
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "concentrate", "expected_revision": 3, "kind": "concentrate"}
        ),
        principal_id="a",
    )
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="b2", actor_id="b", expected_revision=4, encounter_id="fight", maneuver="do_nothing"
        ),
        authenticated_actor_id="b",
    )
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    result = await service.execute(
        cid,
        start.model_copy(update={"id": "complete", "expected_revision": 5, "kind": "complete"}),
        principal_id="a",
    )
    assert result.outcome == "active"
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 2 and dazed(state.resources, "b")
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="a3", actor_id="a", expected_revision=6, encounter_id="fight", maneuver="do_nothing"
        ),
        authenticated_actor_id="a",
    )
    with pytest.raises(ValidationError, match="Dazed"):
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="b3",
                actor_id="b",
                expected_revision=7,
                encounter_id="fight",
                maneuver="move",
                destination=GridPoint(x=3, y=1),
            ),
            authenticated_actor_id="b",
        )
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="b3", actor_id="b", expected_revision=7, encounter_id="fight", maneuver="do_nothing"
        ),
        authenticated_actor_id="b",
    )
    state = play._load(await play.store.read(cid))
    wounded, injury = apply_injury(
        state.resources,
        Wound(
            id="damage",
            actor_id="b",
            expected_revision=state.resources.revision,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert injury.injury == 1 and not dazed(wounded, "b")
    assert not dazed(type(wounded).model_validate_json(wounded.model_dump_json()), "b")


async def start_fight(cid: str, play: PlayService) -> CombatService:
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight-start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="room",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=2, y=1)),
            ),
        ),
        authenticated_actor_id="gm",
    )
    return combat


async def idle(cid: str, play: PlayService, actor: str) -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id=f"idle-{state.revision}",
            actor_id=actor,
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id=actor,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fireball_release_uses_defense_and_exactly_once_injury(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.spells import latest
    from wayfarer.orchestration.combat import ChooseDefense

    cid, play = await setup(tmp_path, combat=True, backend=backend)
    combat = await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(update={"id": "finish", "kind": "complete", "expected_revision": 3}),
        principal_id="a",
    )
    await service.execute(
        cid,
        start.model_copy(update={"id": "release", "kind": "release", "expected_revision": 4}),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is not None
    assert state.encounters[0].pending_defense.allowed == ("none", "dodge")
    # DX10 default -4 =6; five succeeds without a critical, 1d burning rolls four.
    play.rng = RecordedDice([1, 2, 2, 4])
    defense = ChooseDefense(
        id="defense", actor_id="b", expected_revision=5, encounter_id="fight", defense="none"
    )
    results = await asyncio.gather(
        *(combat.execute(cid, defense, authenticated_actor_id="b") for _ in range(3))
    )
    assert all(r == results[0] for r in results)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 6
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert latest(state.resources)["cast"].phase == "ended"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_create_fire_exposure_ends_when_target_moves(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, combat=True)
    combat = await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "create-fire", "channel_id": "create-fire"})
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(update={"id": "finish", "kind": "complete", "expected_revision": 3}),
        principal_id="a",
    )
    await idle(cid, play, "a")
    play.rng = RecordedDice([4])  # One second standing in fire: 1d-1 =3 burning.
    await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 7
    await idle(cid, play, "a")
    play.rng = RecordedDice([2])  # Settle the elapsed exposure before departure.
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="leave",
            actor_id="b",
            expected_revision=7,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=3, y=1),
        ),
        authenticated_actor_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert not any(h.active for h in state.resources.hazards)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_light_illuminates_its_target_until_cancel_without_revealing_facts(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.magic.effects import illuminated

    cid, play = await setup(tmp_path)
    service = SpellService(play)
    before = play._load(await play.store.read(cid))
    await service.execute(cid, command(), principal_id="a")
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, command(2, "complete"), principal_id="a")
    active = play._load(await play.store.read(cid))
    assert illuminated(active, "b") and not illuminated(active, "a")
    assert active.world == before.world
    await service.execute(cid, command(3, "cancel"), principal_id="a")
    ended = play._load(await play.store.read(cid))
    assert not illuminated(ended, "b")
    assert next(p.current for p in ended.resources.pools if p.id == "fp:a") == 8


async def test_fireball_expansion_is_limited_to_three_consecutive_seconds(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.magic.spells import latest
    from wayfarer.errors import ConflictError

    cid, play = await setup(tmp_path, combat=True)
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(
        update={"spell_id": "fireball", "channel_id": "fireball", "energy": 2}
    )
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(update={"id": "complete", "kind": "complete", "expected_revision": 3}),
        principal_id="a",
    )
    for revision in (4, 6):
        await service.execute(
            cid,
            start.model_copy(
                update={"id": f"expand-{revision}", "kind": "expand", "expected_revision": revision}
            ),
            principal_id="a",
        )
        await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["cast"]
    assert effect.energy == 6 and effect.missile_seconds == 3
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="at most three"):
        await service.execute(
            cid,
            start.model_copy(update={"id": "fourth", "kind": "expand", "expected_revision": 8}),
            principal_id="a",
        )
    with pytest.raises(ConflictError, match="held missile"):
        await service.execute(
            cid, command(8).model_copy(update={"cast_id": "second"}), principal_id="a"
        )
    assert await play.store.read(cid) == before


async def test_failed_casting_consciousness_commits_once(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, combat=True, caster_hp=0)
    await start_fight(cid, play)
    service = SpellService(play)
    play.rng = RecordedDice([6, 6, 6])
    start = command(1)
    result = await service.execute(cid, start, principal_id="a")
    assert result.outcome == "interrupted"
    saved = await play.store.read(cid)
    state = play._load(saved)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury and hp.injury.unconscious
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    play.rng = RecordedDice([])
    assert await service.execute(cid, start, principal_id="a") == result
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
