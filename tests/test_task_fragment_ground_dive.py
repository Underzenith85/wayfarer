"""B368/B377 task fragment responses retain trusted ground Step admission."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_area_attacks import grenade
from test_combat_sensory_authority import change
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene
from test_opponent_attack_routes import enroll, luck_source

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion, TakeCombatTurn
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_fragment_records import (
    AmendFragmentResponses,
    ChooseOpponentFragment,
    OpponentFragmentPending,
    PrepareOpponentFragment,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_combat_generations import KEY as TASK_KEY
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import combat_intent, intent_input
from wayfarer.persistence.events import CommandInput
from wayfarer.persistence.replay import verify_commands


async def launched_blast(
    path: Path,
    backend: str = "sqlite",
    *,
    durable: bool = False,
    blocked: bool = False,
    fast: bool = False,
) -> tuple[str, PlayService, PlayState, BlastRecord]:
    definition, purchase = luck_source()
    cid, initial = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,)
        + ((Purchase(definition_id="secondary:basic-move", amount=11),) if fast else ()),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1),
        battlefield=Battlefield(
            id="dock",
            location_id="dock",
            width=4,
            height=4,
            blocked=(GridPoint(x=1, y=1),) if blocked else (),
        ),
        durability=ObjectProfile(construction="unliving", hp=100, dr=100, ht=10, size_modifier=0)
        if durable
        else None,
        object_hp=100 if durable else None,
    )
    play = await enroll(path, backend, cid, initial)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="launch",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            area_aim_point=GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0),
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.due <= state.resources.game_time and not blast.resolved
    return cid, play, state, blast


def responses(*, dive: bool = True) -> tuple[BlastResponse, ...]:
    return tuple(
        BlastResponse(
            actor_id=a,
            cover_dr=0,
            size_modifier=0,
            dive_to=GroundPosition(encounter_id="fight", geometry="grid", x=1, y=1)
            if a == "b" and dive
            else None,
            dive_cover_dr=100 if a == "b" and dive else 0,
            dive_covered_locations=("torso",) if a == "b" and dive else (),
        )
        for a in ("a", "b")
    )


def preparation(state: PlayState, *, dive: bool = True) -> PrepareOpponentFragment:
    return PrepareOpponentFragment(
        id="fragment",
        actor_id="b",
        expected_revision=state.revision,
        launch_command_id="launch",
        resolution=ResolveWeaponExplosion(
            id="fragment",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            blast_id=blasts(state.resources)[0].id,
            responses=responses(dive=dive),
            object_cover={},
            object_sizes={},
            environment="air",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_task_fragment_blocked_step_rejects_before_rng(tmp_path: Path, backend: str) -> None:
    cid, play, state, _ = await launched_blast(tmp_path, backend, blocked=True)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Invalid private task command"):
        await TaskService(play).execute(
            cid,
            preparation(state).model_dump() | {TASK_KEY: ["ground-dive-step"]},
            principal_id="gm",
        )
    with pytest.raises(ValidationError, match="legal ground diving step"):
        await TaskService(play).execute(cid, preparation(state), principal_id="gm")
    assert play.rng.exhausted()
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and events == await play.store.stream(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("succeeds", [True, False])
async def test_task_fragment_dive_success_failure_order_retry(
    tmp_path: Path, backend: str, succeeds: bool
) -> None:
    cid, play, state, _ = await launched_blast(tmp_path, backend)
    command = preparation(state)
    roll = 2 if succeeds else 5
    play.rng = RecordedDice((1, 5, 5, 6, roll, roll, roll, 1, 5, 5, 6))
    result = await TaskService(play).execute(cid, command, principal_id="gm")
    assert play.rng.exhausted()
    opened = play._load(await play.store.read(cid))
    actor = next(p for p in opened.encounters[0].participants if p.actor_id == "b")
    assert actor.position == GridPoint(x=1, y=0)
    assert next(p.current for p in opened.resources.pools if p.id == "hp:b") == (
        10 if succeeds else 9
    )
    pending = snapshot(opened).pending
    assert isinstance(pending, OpponentFragmentPending)
    play.rng = RecordedDice(())
    restarted = build_play(
        tmp_path, play.engine, store=play.store, rng=play.rng, instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="gm") == result
    choice = ChooseOpponentFragment(
        id="choice",
        actor_id="b",
        expected_revision=opened.revision,
        pending_id=pending.id,
        choice="accept",
    )
    receipt = await TaskService(play).execute(cid, choice, principal_id="b")
    settled = play._load(await play.store.read(cid))
    moved = next(p for p in settled.encounters[0].participants if p.actor_id == "b")
    assert moved.position == GridPoint(x=1, y=1) and moved.posture == "prone"
    assert await TaskService(restarted).execute(cid, choice, principal_id="b") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_task_fragment_ground_generation_seed_and_record_identity(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, state, _ = await launched_blast(tmp_path, backend)

    def reverse(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        return state.model_copy(
            update={
                "encounters": (
                    encounter.model_copy(update={"participants": encounter.participants[::-1]}),
                )
            }
        )

    await change(play, cid, reverse)
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    command = preparation(state, dive=False)
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    with monkeypatch.context() as era:
        if legacy:
            era.setattr(task_combat_generations, "FRAGMENT_ACTIVE", frozenset())
        result = await TaskService(play).execute(cid, command, principal_id="gm")
        opened = play._load(await play.store.read(cid))
        pending = snapshot(opened).pending
        assert isinstance(pending, OpponentFragmentPending)
        amend = AmendFragmentResponses(
            id="amend-seed",
            actor_id="b",
            expected_revision=opened.revision,
            pending_id=pending.id,
            responses=(
                BlastResponse(
                    actor_id="a",
                    cover_dr=0,
                    size_modifier=0,
                    dive_to=GroundPosition(encounter_id="fight", geometry="grid", x=0, y=1),
                ),
            ),
        )
        await TaskService(play).execute(cid, amend, principal_id="gm")
        opened = play._load(await play.store.read(cid))
        choice = ChooseOpponentFragment(
            id="choice",
            actor_id="b",
            expected_revision=opened.revision,
            pending_id=pending.id,
            choice="accept",
        )
        await TaskService(play).execute(cid, choice, principal_id="b")
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    for row in records:
        assert row.command_input
        payload = json.loads(intent_input(row.command_input))
        assert (TASK_KEY in payload) is not legacy
        assert task_combat_generations.features(
            CommandInput(row.payload_hash, row.command_input)
        ) == (frozenset() if legacy else frozenset({"ground-dive-step", "secondary-object-blasts"}))
        public_raw = combat_intent(intent_input(row.command_input))
        assert await play.store.duplicate(cid, row.command_id, public_raw) is not None
        with pytest.raises(ConflictError, match="different input"):
            await play.store.duplicate(cid, row.command_id, public_raw + " ")
    play.rng = RecordedDice(())
    assert await TaskService(play).execute(cid, command, principal_id="gm") == result
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 3 and all(c.folded and c.reexecuted for c in checks) and replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("changed", ["occupied", "move"])
async def test_task_amendment_and_choice_recheck_current_ground_route(
    tmp_path: Path, backend: str, changed: str
) -> None:
    cid, play, _, _ = await launched_blast(tmp_path, backend, fast=changed == "move")

    def reverse(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        return state.model_copy(
            update={
                "encounters": (
                    encounter.model_copy(update={"participants": encounter.participants[::-1]}),
                )
            }
        )

    await change(play, cid, reverse)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 5, 5, 6))
    await TaskService(play).execute(cid, preparation(state, dive=False), principal_id="gm")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OpponentFragmentPending)
    assert pending.preparation.progress.actor_ids[
        pending.preparation.progress.actor_index + 1 :
    ] == ("a",)
    later = BlastResponse(
        actor_id="a",
        cover_dr=0,
        size_modifier=0,
        dive_to=GroundPosition(
            encounter_id="fight", geometry="grid", x=0, y=2 if changed == "move" else 1
        ),
    )
    amend = AmendFragmentResponses(
        id="amend",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        responses=(later,),
    )
    play.rng = RecordedDice(())
    receipt = await TaskService(play).execute(cid, amend, principal_id="gm")
    assert await TaskService(play).execute(cid, amend, principal_id="gm") == receipt

    def alter(state: PlayState) -> PlayState:
        if changed == "occupied":
            encounter = state.encounters[0]
            assert isinstance(encounter.spatial, SquareSpatialContext)
            return state.model_copy(
                update={
                    "encounters": (
                        encounter.model_copy(
                            update={
                                "spatial_context": encounter.spatial.model_copy(
                                    update={
                                        "placements": tuple(
                                            p.model_copy(update={"position": GridPoint(x=0, y=1)})
                                            if p.actor_id == "b"
                                            else p
                                            for p in encounter.spatial.placements
                                        )
                                    }
                                ),
                                "participants": tuple(
                                    p.model_copy(update={"position": GridPoint(x=0, y=1)})
                                    if p.actor_id == "b"
                                    else p
                                    for p in encounter.participants
                                ),
                            }
                        ),
                    )
                }
            )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 1}) if p.id == "fp:a" else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )

    await change(play, cid, alter)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    state = play._load(before)
    choice = ChooseOpponentFragment(
        id="choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    with pytest.raises(ValidationError, match="legal ground diving step|limited to one step"):
        await TaskService(play).execute(cid, choice, principal_id="b")
    assert play.rng.exhausted()
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and events == await play.store.stream(cid)
    # The same current route also rejects a new director amendment without draws.
    with pytest.raises(ValidationError, match="legal ground diving step|limited to one step"):
        await TaskService(play).execute(
            cid,
            amend.model_copy(update={"id": "amend-again", "expected_revision": state.revision}),
            principal_id="gm",
        )
    assert before == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_absent_task_generation_keeps_pre_rng_behavior(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, state, _ = await launched_blast(tmp_path, backend, blocked=True)
    before = await play.store.read(cid)
    monkeypatch.setattr(task_combat_generations, "FRAGMENT_ACTIVE", frozenset())
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Replay requested more dice"):
        await TaskService(play).execute(cid, preparation(state), principal_id="gm")
    assert before == await play.store.read(cid)


@pytest.mark.parametrize(
    "flags",
    [
        ["grenade-fuse"],
        ["ground-dive-step", "grenade-fuse"],
        ["ground-dive-step", "ground-dive-step"],
        ["unknown"],
        "ground-dive-step",
    ],
)
def test_fragment_task_feature_namespace_rejects_wrong_flags(flags: object) -> None:
    from wayfarer.persistence.command_inputs import canonical
    from wayfarer.persistence.events import payload_digest

    command = ChooseOpponentFragment(
        id="choice", actor_id="b", expected_revision=1, pending_id="pending", choice="accept"
    )
    raw = canonical(
        {
            "operation": "task-host",
            "principal_id": "b",
            "command": command.model_dump(mode="json"),
            TASK_KEY: flags,
        }
    )
    with pytest.raises(ValidationError):
        task_combat_generations.features(CommandInput(payload_digest({"input": raw}), raw))
    with pytest.raises(ValidationError):
        combat_intent(raw)
