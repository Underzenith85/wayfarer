"""Actual approved builds, SQLite CAS/replay, ability costs and private projections."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from test_abilities import command, context, resources, spec, world
from test_actions import campaign
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.abilities import MODIFIERS, PROFILE, definition
from wayfarer.rules.catalog import RulesCatalog
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.recovery_types import FatigueStatus, RecoveryTask
from wayfarer.simulation.abilities import apply_ability, damage_resistance, effects
from wayfarer.simulation.ability_types import AbilityRules, AbilitySpec
from wayfarer.simulation.actions import ActionEngine, ActionRules, ActorSetup, Wait
from wayfarer.simulation.combat import Battlefield, CombatRules, GridPoint, Placement
from wayfarer.simulation.resources import Pool, ResourceEngine


async def setup(
    tmp_path: Path,
    ability: AbilitySpec,
    *,
    combat: bool = False,
    hp: int = 10,
    injury: InjuryStatus | None = None,
) -> tuple[str, PlayService]:
    package = profile_package(PROFILE, definition(ability))
    catalog = RulesCatalog((package,))
    base = profile_compiler(PROFILE, package=package)
    compiler = CharacterCompiler(
        catalog,
        base.rules,
        replace(base.policy, allow_supernatural=True),
        statistics_profile=PROFILE,
        trait_runtime_hooks=frozenset(
            ["ability:" + ability.kind] + [m.runtime_hook for m in MODIFIERS if m.runtime_hook]
        ),
    )
    ctx = context(ability)
    assert ctx.channel
    configured = ActionRules(
        id="abilities",
        version=1,
        maximum_wait=10000,
        combat=CombatRules(
            id="arena",
            version=1,
            battlefields=(Battlefield(id="room", location_id="room", width=5, height=5),),
        )
        if combat
        else None,
        abilities=AbilityRules(
            id="test-abilities", version=1, abilities=(ability,), channels=(ctx.channel,)
        ),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="power", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        configured,
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "abilities.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    initial_state = play.initial_state(
        initial,
        world(),
        resources(),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(
                    draft=gurps_draft(
                        Purchase(definition_id=ability.definition_id, trait=ctx.options)
                    )
                ),
            ),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),
        ),
    )
    # Explicit fixture opt-in until the profile's complete activation gate passes.
    initial_state = initial_state.model_copy(
        update={
            "resources": initial_state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": hp, "injury": injury or p.injury})
                        if p.id == "hp:a"
                        else p
                        for p in resources().pools
                    )
                    + tuple(
                        Pool(
                            id="fp:" + a,
                            current=10,
                            maximum=10,
                            fatigue=FatigueStatus(profile_id=PROFILE),
                        )
                        for a in ("a", "b")
                    )
                }
            )
        }
    )
    initial["play_json"] = initial_state.model_dump_json()
    await play.store.insert(initial)
    return initial["id"], play


async def test_actual_approved_reading_wait_resistance_private_replay(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec("mind-reading", ("telepathic",)))
    service = AbilityService(play)
    assert (await service.execute(cid, command(), principal_id="a")).outcome == "concentrating"
    with pytest.raises(AuthorizationError):
        await service.execute(cid, command(1, "resolve"), principal_id="b")
    with pytest.raises(ConflictError, match="not complete"):
        await service.execute(cid, command(1, "resolve"), principal_id="a")
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    resolve = command(2, "resolve")
    results = await asyncio.gather(
        *(service.execute(cid, resolve, principal_id="a") for _ in range(3))
    )
    assert results[0].outcome == "active" and len(set(r.model_dump_json() for r in results)) == 1
    restarted = AbilityService(PlayService(play.store, play.engine, rng=RecordedDice([])))
    assert await restarted.execute(cid, resolve, principal_id="a") == results[0]
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert "secret" not in results[0].model_dump_json()
    view = await CampaignAccess(play).read(cid, principal_id="a")
    assert "never revealed" not in str(view)
    events = await CampaignAccess(play).events(cid, principal_id="b")
    assert "I am hungry" not in str(events)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, resolve.model_copy(update={"channel_id": "different"}), principal_id="a"
        )


async def test_malediction_executes_injury_on_signed_profile_pool(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec("burning-malediction", ("malediction-1",)))
    service = AbilityService(play)
    await service.execute(cid, command(), principal_id="a")
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4, 4])
    assert (await service.execute(cid, command(2, "resolve"), principal_id="a")).outcome == "hit"
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 6
    assert state.revision == state.resources.revision == 3


@pytest.mark.parametrize("ticks", [59, 60])
async def test_defense_spends_fp_maintains_expires_and_cancels(tmp_path: Path, ticks: int) -> None:
    cid, play = await setup(tmp_path, spec("damage-resistance", ("costs-fatigue-2",)))
    service = AbilityService(play)
    start = command().model_copy(update={"channel_id": None})
    assert (await service.execute(cid, start, principal_id="a")).outcome == "active"
    state = play._load(await play.store.read(cid))
    assert damage_resistance(state.resources, "a") == 1
    active = effects(state.resources)[0]
    assert active.build_revision
    assert damage_resistance(state.resources, "a", build_revision=active.build_revision) == 1
    assert damage_resistance(state.resources, "a", build_revision="changed-build") == 0
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=ticks),
        authenticated_actor_id="a",
    )
    maintain = command(2, "maintain").model_copy(update={"channel_id": None})
    await service.execute(cid, maintain, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert effects(state.resources)[0].expires_at == 120
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    assert damage_resistance(state.resources.model_copy(update={"game_time": 120}), "a") == 0
    await service.execute(
        cid, command(3, "cancel").model_copy(update={"channel_id": None}), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    assert damage_resistance(state.resources, "a") == 0


@pytest.mark.parametrize("patient", ["a", "b"])
async def test_due_care_blocks_ability_before_any_roll(tmp_path: Path, patient: str) -> None:
    cid, play = await setup(tmp_path, spec("mind-reading"))
    state = play._load(await play.store.read(cid))
    task = RecoveryTask(
        id="care",
        actor_id=patient,
        target_id=patient,
        profile_id=PROFILE,
        kind="natural",
        start=0,
        due=0,
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"recovery_tasks": (task,)})}
    )
    with pytest.raises(ConflictError, match="Settle earned recovery"):
        AbilityService(play).reduce(state, command())


async def test_activated_defense_reduces_authoritative_melee_injury(tmp_path: Path) -> None:
    from test_gurps_melee import setup as melee_setup

    from wayfarer.orchestration.combat import ChooseDefense

    cid, play = await melee_setup(tmp_path, PROFILE, ability_defense=True)
    play.rng = RecordedDice([])
    await AbilityService(play).execute(
        cid,
        command(1).model_copy(update={"ability_id": "trait:shield", "channel_id": None}),
        principal_id="a",
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="swing",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-b",
            mode_id="swing",
            target_id="a",
        ),
        authenticated_actor_id="b",
    )
    play.rng = RecordedDice([4, 4, 4, 3])
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defend", actor_id="a", expected_revision=3, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="a",
    )
    assert result.injury is not None
    assert result.injury.basic_damage == 4
    assert result.injury.injury == 1 and result.injury.hp_after == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_unowned_ability_and_overspend_rejected(tmp_path: Path) -> None:
    ability = spec("damage-resistance", ("costs-fatigue-2",))
    cid, play = await setup(tmp_path, ability)
    with pytest.raises(ValidationError, match="not purchased"):
        await AbilityService(play).execute(
            cid,
            command().model_copy(update={"actor_id": "b", "channel_id": None}),
            principal_id="b",
        )
    base = resources().model_copy(
        update={
            "pools": resources().pools
            + (Pool(id="fp:a", current=1, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),)
        }
    )
    with pytest.raises(ValidationError, match="Insufficient"):
        apply_ability(
            base,
            world(),
            command().model_copy(update={"channel_id": None}),
            ability,
            context(ability),
            rng=RecordedDice([]),
            system=True,
        )


async def test_configuration_digest_includes_ability_channels(tmp_path: Path) -> None:
    _, play = await setup(tmp_path, spec())
    rules = play.engine.rules
    assert rules.abilities
    changed = rules.model_copy(
        update={"abilities": rules.abilities.model_copy(update={"version": 2})}
    )
    other = ActionEngine(play.engine.reviewer, play.engine.resources, changed)
    assert other.digest != play.engine.digest


async def test_ability_concentration_obeys_combat_turn_and_shared_round_clock(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, spec("burning-malediction", ("malediction-1",)), combat=True)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="start",
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
    service = AbilityService(play)
    await service.execute(cid, command(1), principal_id="a")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "b" and state.resources.game_time == 0
    with pytest.raises(ConflictError):
        await service.execute(cid, command(2, "resolve"), principal_id="a")
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="other-turn",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a" and state.resources.game_time == 1
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4, 4])
    assert (await service.execute(cid, command(3, "resolve"), principal_id="a")).outcome == "hit"
    # Completing last turn's concentration does not manufacture another turn.
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a" and state.resources.game_time == 1


@pytest.mark.parametrize("kind", ["activate", "analyze"])
def test_exhaustion_failure_commits_without_ability_roll_or_free_reroll(kind: str) -> None:
    ability = spec("mind-reading")
    base = resources().model_copy(
        update={
            "pools": resources().pools
            + (Pool(id="fp:a", current=0, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),)
        }
    )
    updated, _, result = apply_ability(
        base,
        world(),
        command().model_copy(update={"kind": kind}),
        ability,
        context(ability),
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert result.outcome == "unavailable" and updated.revision == 1
    fatigue = next(p.fatigue for p in updated.pools if p.id == "fp:a")
    assert fatigue is not None and fatigue.collapsed
    again, _, denied = apply_ability(
        updated, world(), command(1), ability, context(ability), rng=RecordedDice([]), system=True
    )
    assert denied.outcome == "unavailable" and again.revision == 2


async def test_max_length_command_id_does_not_break_internal_receipts(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec("damage-resistance", ("costs-fatigue-2",)))
    result = await AbilityService(play).execute(
        cid, command().model_copy(update={"id": "x" * 200, "channel_id": None}), principal_id="a"
    )
    assert result.outcome == "active"


async def test_failed_consciousness_is_committed_without_power_cost_or_reroll(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, spec("damage-resistance", ("costs-fatigue-2",)), hp=0)
    play.rng = RecordedDice([5, 5, 5])
    action = command().model_copy(update={"channel_id": None})
    service = AbilityService(play)
    assert (await service.execute(cid, action, principal_id="a")).outcome == "unavailable"
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.unconscious
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    play.rng = RecordedDice([])
    assert (await service.execute(cid, action, principal_id="a")).outcome == "unavailable"
    assert state.revision == 1 and await play.store.read(cid) == await play.store.replay(cid)


async def test_stunned_actor_cannot_use_concentration_route(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), injury=InjuryStatus(profile_id=PROFILE, stunned=True))
    with pytest.raises(ValidationError, match="Stunned"):
        await AbilityService(play).execute(cid, command(), principal_id="a")
    assert play._load(await play.store.read(cid)).revision == 0


async def test_shock_from_activation_is_retained_until_resolution(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), injury=InjuryStatus(profile_id=PROFILE, shock=4))
    service = AbilityService(play)
    await service.execute(cid, command(), principal_id="a")
    state = play._load(await play.store.read(cid))
    assert effects(state.resources)[0].activation_shock == 4
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    assert (
        await service.execute(cid, command(2, "resolve"), principal_id="a")
    ).outcome == "resisted"


async def test_compiler_rejects_cost_only_attack_without_runtime_modifier(tmp_path: Path) -> None:
    ability = spec("burning-malediction", ("malediction-1",))
    _, play = await setup(tmp_path, ability)
    result = play.engine.reviewer.compiler.compile(
        gurps_draft(Purchase(definition_id=ability.definition_id))
    )
    assert not result.legal and "trait.invalid" in {d.code for d in result.diagnostics}


async def test_portable_ability_binding_roundtrips_and_rejects_duplicate_source(
    tmp_path: Path,
) -> None:
    from pydantic import ValidationError as SchemaError
    from test_wave12 import two_player_graph

    from wayfarer.simulation.scenario_document import PortableGraph
    from wayfarer.simulation.studio import ScenarioGraph

    _, play = await setup(tmp_path, spec())
    binding = play.engine.rules.abilities
    assert binding
    graph = two_player_graph().model_copy(update={"abilities": binding})
    restored = ScenarioGraph.model_validate_json(graph.model_dump_json())
    assert restored.runtime_rules().abilities == binding
    portable = PortableGraph.model_validate_json(graph.model_dump_json(exclude={"actors"}))
    assert (
        PortableGraph.model_validate_json(portable.model_dump_json()).runtime_rules().abilities
        == binding
    )
    with pytest.raises(SchemaError, match="Conflicting actions.abilities"):
        PortableGraph.model_validate(
            portable.model_copy(
                update={
                    "actions": portable.actions.model_copy(
                        update={"abilities": binding.model_copy(update={"version": 2})}
                    )
                }
            )
        )
