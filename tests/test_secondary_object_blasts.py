"""B136/B484 actual blast damage creates and settles one causal object explosion."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_area_attacks import grenade, launch
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.orchestration.opponent_fragment_records import (
    ChooseOpponentFragment,
    OpponentFragmentPending,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import canonical, intent_input
from wayfarer.persistence.replay import verify_commands


async def pending(
    path: Path, backend: str, *, fragments: bool = False, fuse_seconds: int | None = None
) -> tuple[str, PlayService, BlastRecord, ResolveWeaponExplosion]:
    definition, purchase = luck_source()
    mode = grenade()
    if fuse_seconds is not None:
        mode = mode.model_copy(
            update={
                "firearm": FirearmSpec(
                    technology_level=6, action="grenade", fuse_seconds=fuse_seconds
                )
            }
        )
    cid, initial = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=fragments,
        extra_definitions=(definition,) if fragments else (),
        extra_purchases=(purchase,) if fragments else (),
        trait_runtime_hooks=SUPPORTED_HOOKS if fragments else frozenset(),
        aware_of=("a", "b"),
        ranged_mode=mode,
        ranged_scene=scene()
        + (scene()[0].model_copy(update={"attacker_id": "b", "defender_id": "a"}),),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1 if fragments else 0),
        durability=ObjectProfile(
            construction="unliving", hp=10, dr=0, ht=10, size_modifier=0, fragility=("explosive",)
        ),
    )
    play = await enroll(path, backend, cid, initial)
    await launch(
        cid, play, GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0), [3, 3, 3]
    )
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    command = ResolveWeaponExplosion(
        id="parent",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=tuple(
            BlastResponse(actor_id=a, cover_dr=100, covered_locations=("torso",), size_modifier=0)
            for a in ("a", "b")
        ),
        object_cover={"sword-a": 100, "sword-b": 0, "shield-b": 100},
        object_sizes={"sword-a": 0, "sword-b": 0, "shield-b": 0},
        environment="air",
    )
    return cid, play, blast, command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_parent_object_explosion_emits_once_and_child_changes_hp(
    tmp_path: Path, backend: str
) -> None:
    cid, play, parent, command = await pending(tmp_path, backend)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="gm",
        )
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((6, 6, 6, 6, 6, 6, 1, 1)), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await CombatService(failing).execute(cid, command, principal_id="gm")
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and events == await play.store.stream(cid)
    play.rng = RecordedDice((6, 6, 6, 6, 6, 6, 1, 1))
    receipt = await CombatService(play).execute(cid, command, principal_id="gm")
    accepted = await play.store.read(cid)
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=play.rng, instants=play.instants
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="gm") == receipt
    public_raw = canonical({"operation": "combat", "command": command.model_dump(mode="json")})
    assert await play.store.duplicate(cid, command.id, public_raw) == accepted
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, command.id, public_raw + " ")
    assert accepted == await play.store.read(cid)
    state = play._load(await play.store.read(cid))
    result = next(r for r in state.resources.object_results if r.item_id == "sword-b")
    assert result.exploded and result.explosion_dice == 6 and result.condition.hp == -100
    values = blasts(state.resources)
    assert len(values) == 2
    child = next(b for b in values if b.id != parent.id)
    assert not child.resolved and child.payload.dice == 6 and child.due == state.resources.game_time
    assert child.center == GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    assert child.deferred_ticks == parent.deferred_ticks
    play.rng = RecordedDice((1,) * 24)
    resolution = command.model_copy(
        update={
            "id": "child",
            "expected_revision": state.revision,
            "blast_id": child.id,
            "responses": (
                BlastResponse(actor_id="a", cover_dr=0, size_modifier=0),
                BlastResponse(
                    actor_id="b", cover_dr=3, covered_locations=("torso",), size_modifier=0
                ),
            ),
            "object_cover": {"sword-a": 100, "shield-b": 100},
            "object_sizes": {},
        }
    )
    await CombatService(play).execute(cid, resolution, principal_id="gm")
    final = play._load(await play.store.read(cid))
    # B136: six dice; B414: 6/(3*1)=2 at a, and 6-3 cover=3 at b.
    assert next(p.current for p in final.resources.pools if p.id == "hp:a") == 8
    assert next(p.current for p in final.resources.pools if p.id == "hp:b") == 7
    assert len(blasts(final.resources)) == 2 and all(b.resolved for b in blasts(final.resources))
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_seeded_parent_child_preserves_old_generation(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, parent, command = await pending(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: f"{1232:064x}"
    with monkeypatch.context() as era:
        if legacy:
            era.setattr(generations, "ACTIVE", generations.ACTIVE - {"secondary-object-blasts"})
        receipt = await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(r for r in state.resources.object_results if r.item_id == "sword-b").exploded
    assert len(blasts(state.resources)) == (1 if legacy else 2)
    if not legacy:
        child = next(b for b in blasts(state.resources) if b.id != parent.id)
        resolution = command.model_copy(
            update={
                "id": "child-seed",
                "expected_revision": state.revision,
                "blast_id": child.id,
                "object_cover": {"sword-a": 100, "shield-b": 100},
                "object_sizes": {},
            }
        )
        await CombatService(play).execute(cid, resolution, principal_id="gm")
    final = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="gm") == receipt
    records = (await play.store.history(cid))[count:]
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == (1 if legacy else 2) and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secondary_child_can_trigger_and_settle_another_authoritative_cause(
    tmp_path: Path, backend: str
) -> None:
    cid, play, parent, command = await pending(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6, 6, 6, 6, 1, 1))
    await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    child = next(b for b in blasts(state.resources) if b.id != parent.id)
    play.rng = RecordedDice((1,) * 18 + (6, 6, 6) + (1,) * 6)
    second = command.model_copy(
        update={
            "id": "child-chain",
            "expected_revision": state.revision,
            "blast_id": child.id,
            "object_cover": {"sword-a": 100, "shield-b": 0},
            "object_sizes": {},
        }
    )
    await CombatService(play).execute(cid, second, principal_id="gm")
    state = play._load(await play.store.read(cid))
    grandchild = next(b for b in blasts(state.resources) if b.id not in (parent.id, child.id))
    assert grandchild.source_item_id == "shield-b" and grandchild.payload.dice == 6
    assert json.loads(grandchild.evidence)["parent_blast_id"] == child.id
    assert grandchild.center == GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    play.rng = RecordedDice((1,) * 18)
    third = command.model_copy(
        update={
            "id": "grandchild",
            "expected_revision": state.revision,
            "blast_id": grandchild.id,
            "object_cover": {"sword-a": 100},
            "object_sizes": {},
        }
    )
    receipt = await CombatService(play).execute(cid, third, principal_id="gm")
    final = await play.store.read(cid)
    assert len(blasts(play._load(final).resources)) == 3
    assert all(b.resolved for b in blasts(play._load(final).resources))
    assert play.rng.exhausted()
    assert await CombatService(play).execute(cid, third, principal_id="gm") == receipt
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("use_luck", [False, True])
@pytest.mark.parametrize("secret", [False, True])
async def test_fragment_selection_commits_current_position_secondary_cause_atomically(
    tmp_path: Path, backend: str, use_luck: bool, secret: bool
) -> None:
    cid, play, parent, resolution = await pending(tmp_path, backend, fragments=True)
    state = play._load(await play.store.read(cid))
    launch_id = next(
        row.command_id
        for row in await play.store.history(cid)
        if row.command_input
        and json.loads(intent_input(row.command_input)).get("command", {}).get("maneuver")
        == "attack"
    )
    resolution = resolution.model_copy(
        update={
            "responses": (
                BlastResponse(
                    actor_id="a", cover_dr=100, covered_locations=("torso",), size_modifier=0
                ),
                BlastResponse(actor_id="b", cover_dr=0, size_modifier=0),
            )
        }
    )
    opening = PrepareOpponentFragment(
        id=resolution.id,
        actor_id="b",
        expected_revision=state.revision,
        resolution=resolution,
        launch_command_id=launch_id,
        secret=secret,
    )
    play.rng = RecordedDice((1, 5, 5, 6, 1) + (() if secret else (3, 3, 3)))
    begun = await TaskService(play).execute(cid, opening, principal_id="gm")
    state = play._load(await play.store.read(cid))
    pending_roll = snapshot(state).pending
    assert isinstance(pending_roll, OpponentFragmentPending)
    assert len(blasts(state.resources)) == 1 and not blasts(state.resources)[0].resolved
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9

    def move_current_object(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        assert isinstance(encounter.spatial, SquareSpatialContext)
        destination = GridPoint(x=2, y=0)
        return state.model_copy(
            update={
                "encounters": (
                    encounter.model_copy(
                        update={
                            "spatial_context": encounter.spatial.model_copy(
                                update={
                                    "placements": tuple(
                                        p.model_copy(update={"position": destination})
                                        if p.actor_id == "b"
                                        else p
                                        for p in encounter.spatial.placements
                                    )
                                }
                            ),
                            "participants": tuple(
                                p.model_copy(update={"position": destination})
                                if p.actor_id == "b"
                                else p
                                for p in encounter.participants
                            ),
                        }
                    ),
                )
            }
        )

    # Persist a director-side current-position transition during the durable pause.
    await change(play, cid, move_current_object)
    before = await play.store.read(cid)
    state = play._load(before)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    principal = "gm" if secret and not use_luck else "b"
    choice = ChooseOpponentFragment(
        id="fragment-choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending_roll.id,
        choice="use-luck" if use_luck else "accept",
    )
    stream = ((3, 3, 3) if secret else ()) + ((1, 1, 1, 5, 5, 6) if use_luck else ())
    stream += () if use_luck else (3, 3, 3, 1) * 3
    stream += (6, 5, 5, 6, 6, 6, 6) + (1, 5, 5, 6) * 2
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await TaskService(play).execute(cid, choice, principal_id="a")
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            choice.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id=principal,
        )
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(stream), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failing).execute(cid, choice, principal_id=principal)
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and events == await play.store.stream(cid)
    play.rng = RecordedDice(stream)
    receipt = await TaskService(play).execute(cid, choice, principal_id=principal)
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        9 if use_luck else 6
    )
    child = next(b for b in blasts(state.resources) if b.id != parent.id)
    assert child.center == GroundPosition(encounter_id="fight", geometry="grid", x=2, y=0)
    assert child.source_item_id == "sword-b" and child.payload.dice == 6 and not child.resolved
    assert json.loads(child.evidence)["parent_blast_id"] == parent.id
    if secret:
        assert begun.check is None
        if use_luck:
            assert receipt.check is None and receipt.luck is None and receipt.combat_json is None
        else:
            assert receipt.check and receipt.check.dice == (3, 3, 3)
            assert receipt.combat_json is not None
    elif use_luck:
        assert receipt.check and receipt.check.dice == (5, 5, 6)
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=play.rng, instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, choice, principal_id=principal) == receipt
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_seeded_fragment_choice_child_reexecutes_recorded_generation(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, parent, resolution = await pending(tmp_path, backend, fragments=True)
    state = play._load(await play.store.read(cid))
    launch_id = next(
        row.command_id
        for row in await play.store.history(cid)
        if row.command_input
        and json.loads(intent_input(row.command_input)).get("command", {}).get("maneuver")
        == "attack"
    )
    opening = PrepareOpponentFragment(
        id=resolution.id,
        actor_id="b",
        expected_revision=state.revision,
        resolution=resolution,
        launch_command_id=launch_id,
    )
    play.rng = RecordedDice((1, 5, 5, 6, 1, 3, 3, 3))
    await TaskService(play).execute(cid, opening, principal_id="gm")
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(initial)
    pending_roll = snapshot(state).pending
    assert isinstance(pending_roll, OpponentFragmentPending)
    command = ChooseOpponentFragment(
        id="choice-seed",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending_roll.id,
        choice="use-luck",
    )
    play.rng = secrets
    play.seeds = lambda: f"{879613:064x}"
    with monkeypatch.context() as era:
        if legacy:
            era.setattr(
                task_combat_generations,
                "FRAGMENT_ACTIVE",
                task_combat_generations.FRAGMENT_ACTIVE - {"secondary-object-blasts"},
            )
        await TaskService(play).execute(cid, command, principal_id="b")
    state = play._load(await play.store.read(cid))
    assert next(r for r in state.resources.object_results if r.item_id == "sword-b").exploded
    assert len(blasts(state.resources)) == (1 if legacy else 2)
    if not legacy:
        child = next(b for b in blasts(state.resources) if b.id != parent.id)
        play.seeds = lambda: "ab" * 32
        await CombatService(play).execute(
            cid,
            resolution.model_copy(
                update={
                    "id": "child-seed",
                    "expected_revision": state.revision,
                    "blast_id": child.id,
                    "object_cover": {"sword-a": 100, "shield-b": 100},
                    "object_sizes": {},
                }
            ),
            principal_id="gm",
        )
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == (1 if legacy else 2) and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_exploding_armed_item_supersedes_its_actual_later_fuse_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, _ = await pending(tmp_path, backend, fuse_seconds=5)
    for _ in range(2):
        await turn(cid, play, "a", "do_nothing")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    await turn(
        cid,
        play,
        "b",
        "attack",
        item_id="sword-b",
        target_id="a",
        mode_id="ranged",
        area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0),
    )
    from test_gurps_maneuvers import defend

    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play, "a")
    while True:
        current = play._load(await play.store.read(cid))
        first = next(b for b in blasts(current.resources) if b.source_item_id == "sword-a")
        if current.resources.game_time >= first.due:
            break
        await turn(cid, play, current.encounters[0].current_actor_id, "do_nothing")
    state = play._load(await play.store.read(cid))
    values = blasts(state.resources)
    parent = next(b for b in values if b.source_item_id == "sword-a")
    armed = next(b for b in values if b.source_item_id == "sword-b")
    assert parent.due <= state.resources.game_time < armed.due
    command = ResolveWeaponExplosion(
        id="armed-parent",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=parent.id,
        responses=tuple(
            BlastResponse(actor_id=a, cover_dr=100, covered_locations=("torso",), size_modifier=0)
            for a in ("a", "b")
        ),
        object_cover={"sword-a": 100, "sword-b": 0, "shield-b": 100},
        object_sizes={},
        environment="air",
    )
    # Current packet ordering is shield-b, spent source-a, then spent source-b.
    play.rng = RecordedDice((1, 1, 1, 1, 6, 6, 6, 6))
    await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    cancelled = next(b for b in blasts(state.resources) if b.id == armed.id)
    child = next(b for b in blasts(state.resources) if b.id not in (parent.id, armed.id))
    assert cancelled.resolved and cancelled.payload == armed.payload and cancelled.due == armed.due
    assert (
        cancelled.fuse_dice == armed.fuse_dice and cancelled.source_item_id == armed.source_item_id
    )
    assert json.loads(cancelled.evidence)["superseded_by"] == child.id
    assert child.source_item_id == "sword-b" and child.due == state.resources.game_time
    play.rng = RecordedDice((1,) * 24)
    await CombatService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "armed-child",
                "expected_revision": state.revision,
                "blast_id": child.id,
                "object_cover": {"sword-a": 100, "shield-b": 100},
            }
        ),
        principal_id="gm",
    )
    final = await play.store.read(cid)
    assert all(b.resolved for b in blasts(play._load(final).resources))
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="due unresolved blast"):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "denied-old-fuse",
                    "expected_revision": play._load(final).revision,
                    "blast_id": armed.id,
                }
            ),
            principal_id="gm",
        )
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secondary_cause_carries_actual_last_actor_round_debt(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_ranged import weapon

    mode = weapon(thrown=True).model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=4, action="grenade", fuse_seconds=1, malfunction_override=12
            )
        }
    )
    cid, initial = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=scene()
        + (scene()[0].model_copy(update={"attacker_id": "b", "defender_id": "a"}),),
        warhead=ExplosionSpec(dice=6),
        durability=ObjectProfile(
            construction="unliving", hp=10, dr=0, ht=10, size_modifier=0, fragility=("explosive",)
        ),
    )
    play = await enroll(tmp_path, backend, cid, initial)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="ranged")
    play.rng = RecordedDice((4, 4, 4, 5, 5, 5))
    await defend(cid, play, "a")
    state = play._load(await play.store.read(cid))
    parent = blasts(state.resources)[0]
    assert state.resources.game_time == parent.due == 0 and parent.deferred_ticks == 1
    command = ResolveWeaponExplosion(
        id="debt-parent",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=parent.id,
        responses=tuple(
            BlastResponse(actor_id=a, cover_dr=100, covered_locations=("torso",), size_modifier=0)
            for a in ("a", "b")
        ),
        object_cover={"sword-a": 0, "shield-b": 100},
        environment="air",
    )
    play.rng = RecordedDice((1,) * 12 + (6,) * 9 + (1,) * 6)
    await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    child = next(b for b in blasts(state.resources) if b.id != parent.id)
    assert state.resources.game_time == 0 and child.deferred_ticks == 1 and child.due == 0
    assert child.source_item_id == "sword-a" and child.payload.dice == 6
    resolution = command.model_copy(
        update={
            "id": "debt-child",
            "expected_revision": state.revision,
            "blast_id": child.id,
            "object_cover": {"shield-b": 100},
        }
    )
    play.rng = RecordedDice((1,) * 18)
    receipt = await CombatService(play).execute(cid, resolution, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 1 and all(b.resolved for b in blasts(state.resources))
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, resolution, principal_id="gm") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_primary_detonating_carrier_does_not_add_same_warhead_twice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, parent, command = await pending(tmp_path, backend)
    command = command.model_copy(
        update={"object_cover": {"sword-a": 0, "sword-b": 100, "shield-b": 100}}
    )
    play.rng = RecordedDice((6, 6, 1, 1, 6, 6, 6, 6))
    receipt = await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(r for r in state.resources.object_results if r.item_id == "sword-a").exploded
    assert len(blasts(state.resources)) == 1 and blasts(state.resources)[0].id == parent.id
    assert blasts(state.resources)[0].resolved
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="gm") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)
