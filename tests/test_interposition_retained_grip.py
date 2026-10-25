"""B370–371/B375 actual retained grips cannot silently translate with a Step."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_interposition_current_step import consequence_seed
from test_unarmed import action, defend

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield, HexFacing
from wayfarer.engine.world import Fact
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def pending(
    path: Path, backend: str, *, hexes: bool, colocated: bool
) -> tuple[str, PlayService, ChooseDefense]:
    positions: tuple[tuple[str, int, int, HexFacing], ...] = (
        (("a", 3, 1, 4), ("b", 2, 0, 0), ("c", 4, 0, 3), ("d", 3, 0, 3))
        if colocated
        else (("a", 0, 0, 0), ("b", 1, 0, 3), ("c", 3, 0, 3), ("d", 2, 0, 3))
    )
    board: Battlefield | HexBattlefield = (
        HexBattlefield(
            id="field",
            location_id="dock",
            coordinate_system="hex-axial-v1",
            profile_id="gurps-basic-set-4e-2004",
            baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
            cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(5) for r in range(3)),
        )
        if hexes
        else Battlefield(id="field", location_id="dock", width=5, height=3)
    )
    authored_world = world()
    authored_world = replace(
        authored_world,
        entities=authored_world.entities
        + tuple(
            replace(
                next(entity for entity in authored_world.entities if entity.id == "b"), id=actor
            )
            for actor in ("c", "d")
        ),
    )
    authored_world = replace(
        authored_world,
        facts=authored_world.facts
        + tuple(
            Fact("presence:" + actor, actor, "present", "dock") for actor in ("a", "b", "c", "d")
        ),
    )
    for actor in ("a", "b", "c", "d"):
        for target in ("a", "b", "c", "d"):
            authored_world = authored_world.learn(actor, "presence:" + target)
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        fourth_actor=True,
        unarmed_fixture=True,
        free_defender_hand=True,
        battlefield=board,
        aware_of=("a", "b", "c", "d"),
        runtime_world=authored_world,
        placements=tuple(
            Placement(actor_id=actor, position=Hex(q=x, r=y), hex_facing=facing)
            if hexes
            else Placement(
                actor_id=actor,
                position=GridPoint(x=x, y=y),
                facing="east" if facing == 0 else "north" if facing == 4 else "west",
            )
            for actor, x, y, facing in positions
        ),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    await turn(cid, play, "a", "do_nothing")
    if colocated:
        play.rng = RecordedDice([3, 3, 3])
        await action(cid, play, "b", "grapple", target="d", hands=("left-hand",), enter=True)
        await defend(cid, play)
    else:
        await turn(cid, play, "b", "do_nothing")
    play.rng = RecordedDice([3, 3, 3])
    await action(cid, play, "c", "grapple", target="d", hands=("left-hand",), enter=True)
    await defend(cid, play)
    await turn(cid, play, "d", "do_nothing")
    play.rng = RecordedDice([3] * (8 if colocated else 3))
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="swing", target_id="b")
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert any(g.holder_id == "c" and g.target_id == "d" for g in encounter.grips)
    protector = next(p for p in encounter.participants if p.actor_id == "c")
    friend = next(p for p in encounter.participants if p.actor_id == "b")
    victim = next(p for p in encounter.participants if p.actor_id == "d")
    assert not protector.grappled and victim.grappled
    assert protector.position == victim.position
    assert (protector.position == friend.position) == colocated
    return (
        cid,
        play,
        ChooseDefense(
            id="holder-protect",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            sacrificial_for="b",
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hexes", [False, True])
@pytest.mark.parametrize("colocated", [False, True])
async def test_actual_holder_requires_release_for_translation_but_can_protect_without_moving(
    tmp_path: Path, backend: str, hexes: bool, colocated: bool
) -> None:
    cid, play, command = await pending(tmp_path, backend, hexes=hexes, colocated=colocated)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    if not colocated:
        play.rng = RecordedDice([])
        with pytest.raises(ValidationError, match="Release the retained grapple"):
            await CombatService(play).execute(cid, command, principal_id="c")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 1])
    result = await CombatService(play).execute(cid, command, principal_id="c")
    final = await play.store.read(cid)
    updated = play._load(final)
    assert result.injury and result.injury.injury > 0
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in updated.resources.pools if p.id == "hp:c") < 20
    assert updated.encounters[0].grips == state.encounters[0].grips
    assert tuple(p.position for p in updated.encounters[0].participants) == tuple(
        p.position for p in state.encounters[0].participants
    )
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_square_holder_keeps_late_refusal_without_early_release_policy(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, command = await pending(tmp_path, backend, hexes=False, colocated=False)
    before, history = await play.store.read(cid), await play.store.history(cid)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"interposition-current-step"})
    # The old square path tried to roll, then eventually failed canonical grip
    # validation. Its absent feature remains unchanged; it never silently gains
    # an automatic release or a lawful dragging contract.
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="c")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 1])
    with pytest.raises(
        ValidationError, match="Grip requires an explicit shared close-combat position"
    ):
        await CombatService(play).execute(
            cid, command.model_copy(update={"id": "holder-late"}), principal_id="c"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hexes", [False, True])
async def test_no_translation_holder_response_reexecutes_with_real_retained_grips(
    tmp_path: Path, backend: str, hexes: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, command = await pending(tmp_path, backend, hexes=hexes, colocated=True)
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, consequence_seed
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.injury and result.injury.injury > 0
    final = await play.store.read(cid)
    assert play._load(final).encounters[0].grips == state.encounters[0].grips
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"interposition-current-step"})
    replayed, checks = await verify_commands(
        initial,
        (await play.store.history(cid))[count:],
        [e for e in await play.store.stream(cid) if e.command_id == command.id],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "holder-seed-only"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == final
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="c") == result
