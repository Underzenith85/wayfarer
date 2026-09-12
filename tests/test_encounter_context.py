"""Scene ownership and subgroup clocks through the existing authoritative services."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_actions import Dice, actor_setup, campaign
from test_combat import combat_engine, resources, start
from test_scenes import configured

from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.encounter_context import (
    EncounterSceneBinding,
    activity_for,
    bind_scene,
)
from wayfarer.engine.simulation.campaign.party import (
    PartyRules,
    PartyState,
    PendingEffect,
    QueuedActivity,
    migrate,
)
from wayfarer.engine.simulation.campaign.scenes import ActorScene, Scene
from wayfarer.engine.simulation.combat.combat import Encounter, GridPoint, Placement
from wayfarer.engine.simulation.combat.maneuvers import WaitInterrupt, WaitTrigger
from wayfarer.engine.simulation.combat.unarmed_records import PendingUnarmed
from wayfarer.engine.simulation.resources import Owner
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import CombatService, EndEncounter, TakeCombatTurn
from wayfarer.orchestration.encounter_scenes import (
    EncounterSceneService,
    MigrateEncounterScenes,
)
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def setup(
    tmp_path: Path, *, ambiguous: bool = False, legacy: bool = False, scene_less: bool = False
) -> tuple[str, PlayService]:
    base, world = configured()
    combat = combat_engine().rules.combat
    assert combat is not None
    scenes = base.rules.scenes
    assert scenes is not None
    if ambiguous:
        scenes = scenes.model_copy(
            update={
                "scenes": scenes.scenes
                + (Scene(id="other-dock", version=1, location_id="dock", title="Other context"),)
            }
        )
    world = replace(
        world,
        entities=world.entities
        + tuple(Entity(a, EntityKind.ACTOR, a, location_id="dock") for a in ("c", "d")),
    )
    engine = ActionEngine(
        base.reviewer,
        base.resources.for_world(world),
        base.rules.model_copy(
            update={
                "combat": combat,
                "scenes": None if scene_less else scenes,
                "party": None if scene_less else PartyRules(id="party", version=1),
            }
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "encounters.sqlite", 10), engine, rng=Dice())
    initial = campaign(engine)
    seed = resources().model_copy(
        update={
            "owners": resources().owners
            + (
                Owner(actor_id="c", capacity=100),
                Owner(actor_id="d", capacity=100),
            )
        }
    )
    state = play.initial_state(
        initial,
        world,
        seed,
        tuple(actor_setup().model_copy(update={"actor_id": a}) for a in ("a", "b", "c", "d")),
        (
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="scout", role="player", actor_ids=("c",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    if not scene_less:
        state = migrate(
            state.model_copy(
                update={
                    "actor_scenes": tuple(
                        ActorScene(actor_id=a, scene_id="dock-scene") for a in ("a", "b", "c", "d")
                    ),
                    "party": PartyState(),
                }
            )
        )
    if legacy:
        assert engine.combat is not None
        encounter = engine.combat.start(
            "fight",
            "dock-fight",
            (
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=1, y=0)),
            ),
            {"a": 10, "b": 10},
            world,
            state.resources,
            frozenset({"a", "b", "c", "d"}),
        )
        state = state.model_copy(update={"encounters": (encounter,)})
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    return initial["id"], play


async def load(play: PlayService, cid: str) -> PlayState:
    return play._load(await play.store.read(cid))


async def open_fight(play: PlayService, cid: str) -> None:
    await CombatService(play).execute(
        cid,
        start().model_copy(
            update={
                "scene_id": "dock-scene",
                "placements": (
                    Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                    Placement(actor_id="b", position=GridPoint(x=1, y=0)),
                ),
            }
        ),
        authenticated_actor_id="gm",
    )


async def test_scene_binding_is_persisted_and_completed_encounter_survives_travel(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    await open_fight(play, cid)
    state = await load(play, cid)
    encounter = state.encounters[0]
    assert (encounter.version, encounter.scene_id) == (2, "dock-scene")
    ctx = activity_for(state, "a")
    assert ctx.encounter == encounter and ctx.group_encounter == encounter
    assert ctx.spatial_kind == "square"
    assert play.engine.rules.combat is not None
    battlefield = ctx.battlefield(play.engine.rules.combat)
    assert battlefield is not None and battlefield.id == "dock-fight"
    spectator = activity_for(state, "c")
    assert spectator.encounter is None and spectator.group_encounter == encounter
    assert spectator.spatial_kind is None
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="Resolved"
        ),
        authenticated_actor_id="gm",
    )
    state = await load(play, cid)
    moved = state.model_copy(
        update={
            "world": replace(
                state.world,
                entities=tuple(
                    replace(e, location_id="alley") if e.id in {"a", "b", "c", "d"} else e
                    for e in state.world.entities
                ),
            ),
            "actor_scenes": tuple(
                c.model_copy(update={"scene_id": "alley-scene"}) for c in state.actor_scenes
            ),
            "party": state.party.model_copy(
                update={
                    "groups": tuple(
                        g.model_copy(update={"scene_id": "alley-scene"}) for g in state.party.groups
                    )
                }
            ),
        }
    )
    play.engine.validate(moved)
    assert moved.encounters[0].scene_id == "dock-scene"
    assert activity_for(moved, "a").encounter is None
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_scene_invariants_and_duplicate_subgroup_encounters(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ambiguous=True)
    await open_fight(play, cid)
    state = await load(play, cid)
    # Even without party coordination, same world location is insufficient.
    bad = state.model_copy(
        update={
            "party": PartyState(),
            "actor_scenes": tuple(
                c.model_copy(update={"scene_id": "other-dock"}) if c.actor_id == "b" else c
                for c in state.actor_scenes
            ),
        }
    )
    with pytest.raises(ValidationError, match="one scene"):
        play.engine.validate(bad)
    bad = state.model_copy(
        update={
            "world": replace(
                state.world,
                entities=tuple(
                    replace(e, location_id="alley") if e.id == "a" else e
                    for e in state.world.entities
                ),
            )
        }
    )
    with pytest.raises(ValidationError):
        play.engine.validate(bad)
    before = await play.store.read(cid)
    second = start(revision=1).model_copy(
        update={
            "encounter_id": "second",
            "id": "second",
            "scene_id": "dock-scene",
            "placements": (
                Placement(actor_id="c", position=GridPoint(x=0, y=0)),
                Placement(actor_id="d", position=GridPoint(x=1, y=0)),
            ),
        }
    )
    with pytest.raises(ValidationError, match="Subgroup participates"):
        await CombatService(play).execute(cid, second, authenticated_actor_id="gm")
    assert await play.store.read(cid) == before
    duplicate = state.encounters[0].model_copy(update={"id": "duplicate"})
    with pytest.raises(ValidationError, match="multiple active encounters"):
        play.engine.validate(
            state.model_copy(update={"encounters": state.encounters + (duplicate,)})
        )


async def test_unique_legacy_normalization_preserves_historical_checkpoint(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, legacy=True)
    original = await play.store.read(cid)
    state = await load(play, cid)
    assert state.encounters[0].version == 2
    assert state.encounters[0].scene_id == "dock-scene"
    assert await play.store.read(cid) == original
    assert await play.store.replay(cid) == original
    old = PlayState.model_validate_json(original["play_json"])
    assert state.resources == old.resources and state.world == old.world
    assert state.configuration_digest == old.configuration_digest


async def test_ambiguous_migration_is_explicit_authorized_atomic_and_retry_safe(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, ambiguous=True, legacy=True)
    before = await play.store.read(cid)
    state = await load(play, cid)
    assert state.encounters[0].version == 1
    with pytest.raises(ValidationError, match="explicit scene mapping"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="turn", actor_id="a", expected_revision=0, encounter_id="fight", maneuver="wait"
            ),
            authenticated_actor_id="a",
        )
    command = MigrateEncounterScenes(
        id="bind",
        actor_id="gm",
        expected_revision=0,
        bindings=(EncounterSceneBinding(encounter_id="fight", scene_id="dock-scene"),),
    )
    access = CampaignAccess(play)
    with pytest.raises(AuthorizationError):
        await access.execute(cid, command.model_dump(mode="json"), principal_id="alice")
    with pytest.raises(ValidationError):
        await EncounterSceneService(play).execute(
            cid,
            command.model_copy(
                update={
                    "bindings": (
                        EncounterSceneBinding(encounter_id="fight", scene_id="alley-scene"),
                    )
                }
            ),
            authenticated_gm_id="gm",
        )
    assert await play.store.read(cid) == before
    await access.execute(cid, command.model_dump(mode="json"), principal_id="gm")
    after = await play.store.read(cid)
    await access.execute(cid, command.model_dump(mode="json"), principal_id="gm")
    assert await play.store.read(cid) == after
    migrated = await load(play, cid)
    assert migrated.encounters[0].scene_id == "dock-scene"
    assert migrated.resources.game_time == state.resources.game_time
    assert migrated.world == state.world and migrated.party == state.party
    assert migrated.configuration_digest == state.configuration_digest
    with pytest.raises(ConflictError):
        await EncounterSceneService(play).execute(
            cid, command.model_copy(update={"id": "stale"}), authenticated_gm_id="gm"
        )
    restarted = PlayService(play.store, play.engine, rng=Dice())
    assert await load(restarted, cid) == migrated
    assert await play.store.replay(cid) == after


async def test_scene_less_legacy_requires_configured_mapping_without_inventing_scene(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, scene_less=True, legacy=True)
    state = await load(play, cid)
    assert state.encounters[0].scene_id is None
    assert state.encounters[0].version == 1
    assert play.engine.rules.combat is not None
    with pytest.raises(ValidationError, match="explicit campaign migration"):
        bind_scene(state.encounters[0], None, play.engine.rules.combat, "invented")
    assert '"scene_id"' not in state.encounters[0].model_dump_json()
    with pytest.raises(ValueError):
        Encounter.model_validate(state.encounters[0].model_dump() | {"version": 2})


async def test_spectator_split_preserves_combat_and_can_queue_independent_activity(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    await open_fight(play, cid)
    state = await load(play, cid)
    split = PartyCommand(
        id="split", actor_id="c", kind="split_party", expected_revision=1, target_id="scouts"
    )
    access = CampaignAccess(play)
    await access.execute(cid, split.model_dump(mode="json"), principal_id="scout")
    after = await load(play, cid)
    assert after.encounters == state.encounters and after.world == state.world
    assert after.resources.game_time == 0
    assert len(after.party.groups) == 2
    remaining_group = activity_for(after, "a").group
    assert remaining_group is not None and remaining_group.generation == 1
    assert activity_for(after, "c").scene_id == activity_for(after, "a").scene_id
    with pytest.raises(ConflictError):
        await access.execute(
            cid,
            split.model_copy(update={"id": "stale"}).model_dump(mode="json"),
            principal_id="scout",
        )
    queued = PartyCommand(
        id="wait",
        actor_id="c",
        kind="queue_activity",
        expected_revision=2,
        activity_json=Wait(
            id="inner", actor_id="c", expected_revision=2, ticks=2
        ).model_dump_json(),
    )
    await access.execute(cid, queued.model_dump(mode="json"), principal_id="scout")
    after = await load(play, cid)
    assert activity_for(after, "c").queued is not None
    assert after.resources.game_time == 0
    assert await play.store.replay(cid) == await play.store.read(cid)


@pytest.mark.parametrize(
    "blocker",
    [
        "active_actor",
        "paused",
        "queue",
        "effect",
        "frontier",
        "defense",
        "unarmed",
        "wait_interrupt",
        "blocked",
    ],
)
async def test_spectator_split_cannot_bypass_barriers(tmp_path: Path, blocker: str) -> None:
    cid, play = await setup(tmp_path)
    await open_fight(play, cid)
    state = await load(play, cid)
    group = state.party.groups[0]
    actor_id = "a" if blocker == "active_actor" else "c"
    if blocker == "paused":
        state = state.model_copy(
            update={
                "party": state.party.model_copy(
                    update={"groups": (group.model_copy(update={"paused": True}),)}
                )
            }
        )
    elif blocker == "frontier":
        state = state.model_copy(
            update={
                "party": state.party.model_copy(
                    update={"groups": (group.model_copy(update={"ready_through": 1}),)}
                )
            }
        )
    elif blocker == "queue":
        q = QueuedActivity(
            id="q",
            group_id=group.id,
            actor_id="c",
            generation=0,
            start=0,
            due=0,
            family="action",
            command_json="{}",
        )
        state = state.model_copy(update={"party": state.party.model_copy(update={"queue": (q,)})})
    elif blocker == "effect":
        effect = PendingEffect(id="signal", due=1, fact_id="clue", recipient_actor_ids=("c",))
        state = state.model_copy(
            update={"party": state.party.model_copy(update={"effects": (effect,)})}
        )
    elif blocker == "defense":
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="attack",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                target_id="b",
            ),
            authenticated_actor_id="a",
        )
        state = await load(play, cid)
    elif blocker in ("unarmed", "wait_interrupt", "blocked"):
        encounter = state.encounters[0]
        if blocker == "unarmed":
            encounter = encounter.model_copy(
                update={
                    "pending_unarmed": PendingUnarmed(
                        id="grapple",
                        actor_id="a",
                        target_id="b",
                        action="grapple",
                        skill="attribute:dx",
                        allowed=("none",),
                    )
                }
            )
        elif blocker == "wait_interrupt":
            encounter = encounter.model_copy(
                update={
                    "wait_interrupt": WaitInterrupt(
                        waiter_id="b",
                        actor_id="a",
                        turn_index=0,
                        command_json="{}",
                        declaration=WaitTrigger(action="attack", item_id="sword-b"),
                    )
                }
            )
        else:
            encounter = encounter.model_copy(update={"blocked_reason": "Adjudication required"})
        state = state.model_copy(update={"encounters": (encounter,)})
    with pytest.raises(ConflictError):
        PartyService(play).reduce(
            state,
            PartyCommand(
                id="split",
                actor_id=actor_id,
                kind="split_party",
                expected_revision=state.revision,
                target_id="scouts",
            ),
        )


async def test_scene_less_campaign_adopts_explicit_actor_and_encounter_mappings(
    tmp_path: Path,
) -> None:
    from wayfarer.orchestration.advancement import ApplyMigration, MigrationService

    cid, old_play = await setup(tmp_path, legacy=True, scene_less=True)
    old = await load(old_play, cid)
    base, _ = configured()
    assert base.rules.scenes is not None
    scenes = base.rules.scenes.model_copy(
        update={
            "scenes": base.rules.scenes.scenes
            + (Scene(id="other-dock", version=1, location_id="dock", title="Other context"),)
        }
    )
    target = PlayService(
        old_play.store,
        ActionEngine(
            old_play.engine.reviewer,
            old_play.engine.resources,
            old_play.engine.rules.model_copy(
                update={"scenes": scenes, "party": PartyRules(id="party", version=1)}
            ),
        ),
    )
    service = MigrationService(old_play, target)
    command = ApplyMigration(
        id="adopt-scenes",
        actor_id="gm",
        expected_revision=0,
        expected_from_digest=old_play.engine.digest,
        reason="Adopt explicitly mapped scenes",
    )
    before = await old_play.store.read(cid)
    with pytest.raises(ValidationError, match="explicit actor scene mapping"):
        await service.apply(cid, command, authenticated_gm_id="gm")
    command = command.model_copy(
        update={
            "actor_scenes": tuple(
                ActorScene(actor_id=a.actor_id, scene_id="dock-scene") for a in old.actors
            )
        }
    )
    with pytest.raises(ValidationError, match="ambiguous encounter"):
        await service.apply(cid, command, authenticated_gm_id="gm")
    assert await old_play.store.read(cid) == before
    command = command.model_copy(
        update={
            "encounter_scenes": (
                EncounterSceneBinding(encounter_id="fight", scene_id="dock-scene"),
            )
        }
    )
    first = await service.apply(cid, command, authenticated_gm_id="gm")
    assert await service.apply(cid, command, authenticated_gm_id="gm") == first
    after = await load(target, cid)
    assert after.encounters[0].scene_id == "dock-scene"
    assert (
        len(after.party.groups) == 1
        and after.party.groups[0].ready_through == old.resources.game_time
    )
    assert after.world == old.world and after.resources.game_time == old.resources.game_time
    assert after.configuration_digest == target.engine.digest
    assert after.resources.items == old.resources.items
    assert await target.store.replay(cid) == await target.store.read(cid)


async def test_two_disjoint_groups_can_each_own_an_encounter(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.campaign.party import Subgroup

    cid, play = await setup(tmp_path)
    await open_fight(play, cid)
    state = await load(play, cid)
    assert play.engine.combat is not None
    second = play.engine.combat.start(
        "other",
        "dock-fight",
        (
            Placement(actor_id="c", position=GridPoint(x=0, y=0)),
            Placement(actor_id="d", position=GridPoint(x=1, y=0)),
        ),
        {"c": 10, "d": 10},
        state.world,
        state.resources,
        frozenset({"a", "b", "c", "d"}),
    )
    second = bind_scene(second, play.engine.rules.scenes, play.engine.combat.rules)
    groups = tuple(
        Subgroup(id=name, actor_ids=actors, scene_id="dock-scene", ready_through=0)
        for name, actors in (("one", ("a", "b")), ("two", ("c", "d")))
    )
    state = state.model_copy(
        update={
            "encounters": state.encounters + (second,),
            "party": state.party.model_copy(update={"groups": groups}),
        }
    )
    play.engine.validate(state)
    assert activity_for(state, "c").encounter == second
    stale = QueuedActivity(
        id="old",
        group_id="two",
        actor_id="c",
        generation=1,
        start=0,
        due=0,
        command_json="{}",
        family="action",
    )
    with pytest.raises(ValidationError, match="activity binding"):
        play.engine.validate(
            state.model_copy(update={"party": state.party.model_copy(update={"queue": (stale,)})})
        )


def test_encounter_scene_command_contracts_are_current() -> None:
    from scripts.encounter_scene_contracts import PATH, contract

    assert PATH.read_text() == contract()
