"""B368/B375 and B419/B426: the protecting actor uses current Move for a Step."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_combat_sensory_authority import change
from test_gurps_maneuvers import turn
from test_gurps_melee import setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

FEATURE = "interposition-current-step"
Condition = Literal["none", "fp", "hp", "both"]


def current_condition(state: PlayState, condition: Condition) -> PlayState:
    resources = state.resources
    if condition in ("fp", "both"):
        resources, result = apply_fatigue(
            resources,
            FatigueCost(
                id="spent-fp", actor_id="c", expected_revision=resources.revision, amount=7
            ),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
        assert result.allowed
    if condition in ("hp", "both"):
        resources, _ = apply_injury(
            resources,
            Wound(
                id="current-wound",
                actor_id="c",
                expected_revision=resources.revision,
                basic_damage=14,
                resistance=0,
                damage_type="burn",
            ),
            ht=10,
            rng=RecordedDice([3, 3, 3]),
            system=True,
        )
        hp = next(p for p in resources.pools if p.id == "hp:c")
        assert hp.maximum == 20 and hp.current == 6
        assert hp.injury and not hp.injury.stunned and not hp.injury.prone
    return state.model_copy(update={"resources": resources})


async def pending(
    path: Path,
    backend: str,
    *,
    hexes: bool = False,
    yards: int = 2,
    condition: Condition = "fp",
) -> tuple[str, PlayService, ChooseDefense]:
    board: Battlefield | HexBattlefield = (
        HexBattlefield(
            id="field",
            location_id="dock",
            coordinate_system="hex-axial-v1",
            profile_id="gurps-basic-set-4e-2004",
            baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
            cells=tuple(Cell(position=Hex(q=q, r=0)) for q in range(5)),
        )
        if hexes
        else Battlefield(id="field", location_id="dock", width=5, height=1)
    )
    placements = tuple(
        Placement(actor_id=actor, position=Hex(q=x, r=0), hex_facing=0 if actor == "a" else 3)
        if hexes
        else Placement(
            actor_id=actor,
            position=GridPoint(x=x, y=0),
            facing="east" if actor == "a" else "west",
        )
        for actor, x in (("a", 0), ("b", 1), ("c", 1 + yards))
    )
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        placements=placements,
        battlefield=board,
        aware_of=("a", "b", "c"),
        runtime_world=world().learn("a", "promise"),
        extra_purchases=(Purchase(definition_id="secondary:basic-move", amount=11),),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    for actor_id in ("a", "b", "c"):
        await turn(cid, play, actor_id, "do_nothing")
    play.rng = RecordedDice([3, 3, 3])
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="swing", target_id="b")
    await change(play, cid, lambda state: current_condition(state, condition))
    state = play._load(await play.store.read(cid))
    actor = next(p for p in state.encounters[0].participants if p.actor_id == "c")
    assert actor.movement_allowance == 11
    assert (
        movement(play.rules_context, state, "c")
        == {
            "none": 11,
            "fp": 6,
            "hp": 6,
            "both": 3,
        }[condition]
    )
    return (
        cid,
        play,
        ChooseDefense(
            id="current-step",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hexes", [False, True])
@pytest.mark.parametrize("condition", ["none", "fp", "hp", "both"])
async def test_current_hp_fp_determine_actual_two_yard_step_admission(
    tmp_path: Path, backend: str, hexes: bool, condition: Condition
) -> None:
    cid, play, command = await pending(tmp_path, backend, hexes=hexes, condition=condition)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="c",
        )
    if condition != "none":
        with pytest.raises(ValidationError, match="beyond the protecting actor's step"):
            await CombatService(play).execute(cid, command, principal_id="c")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 2])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.injury and result.injury.injury > 0
    final = await play.store.read(cid)
    state = play._load(final)
    protector, friend = state.encounters[0].participants[2], state.encounters[0].participants[1]
    assert protector.position == friend.position
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") < 20
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


def consequence_seed() -> str:
    for number in range(10000):
        value = f"{number:064x}"
        rng = SeededRandom(value)
        attack_roll = sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge_roll = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if attack_roll == 9 and dodge_roll == 4 and damage == 1:
            return value
    raise AssertionError("No interposition source-consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_captured_step_generation_preserves_direct_and_task_reexecution(
    tmp_path: Path, backend: str, typed: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fresh low-FP protectors can still take one yard. Old captured semantics
    # retain the formerly admitted two-yard response despite current Move 6.
    cid, play, response = await pending(tmp_path, backend, yards=1 if enabled else 2)
    if typed:
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=response.expected_revision,
                running=True,
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
        response = response.model_copy(update={"expected_revision": state.revision})
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    command = (
        PrepareOwnerDamage(
            id=response.id,
            actor_id="c",
            expected_revision=response.expected_revision,
            response=response,
        )
        if typed
        else response
    )
    module = task_combat_generations if typed else generations
    features = module.ACTIVE
    monkeypatch.setattr(module, "ACTIVE", features if enabled else features - {FEATURE})
    play.rng, play.seeds = secrets, consequence_seed
    service = TaskService(play) if typed else CombatService(play)
    result = await service.execute(cid, command, principal_id="c")
    if typed:
        staged = play._load(await play.store.read(cid))
        damage = snapshot(staged).pending
        assert isinstance(damage, InventoryDamagePending) and damage.actor_id == "a"
        assert next(p.current for p in staged.resources.pools if p.id == "hp:c") == 20
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="accept-damage",
                actor_id="a",
                expected_revision=staged.revision,
                pending_id=damage.id,
                choice="accept",
            ),
            principal_id="a",
        )
    final = await play.store.read(cid)
    final_state = play._load(final)
    assert next(p.current for p in final_state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in final_state.resources.pools if p.id == "hp:c") < 20
    assert final_state.encounters[0].participants[2].position == GridPoint(x=1, y=0)
    records = (await play.store.history(cid))[count:]
    raw = records[0].command_input
    assert raw
    payload = replay_payload(raw)
    assert isinstance(payload, dict)
    key = task_combat_generations.KEY if typed else generations.KEY
    assert (FEATURE in payload[key]) == enabled
    monkeypatch.setattr(module, "ACTIVE", features - {FEATURE} if enabled else features)
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == (2 if typed else 1) and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    service = TaskService(restarted) if typed else CombatService(restarted)
    assert await service.execute(cid, command, principal_id="c") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
