"""B368/B375 interposition admits a real step before defense dice or injury."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_gurps_melee import setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hexes", [False, True])
@pytest.mark.parametrize("blocked", [False, True])
async def test_interposition_cannot_teleport_through_terrain(
    tmp_path: Path, hexes: bool, blocked: bool, backend: str
) -> None:
    board: Battlefield | HexBattlefield
    placements: tuple[Placement, ...]
    if hexes:
        board = HexBattlefield(
            id="field",
            location_id="dock",
            coordinate_system="hex-axial-v1",
            profile_id="gurps-basic-set-4e-2004",
            baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
            cells=tuple(
                Cell(position=Hex(q=q, r=r), blocked=r != 0 or (blocked and q == 2))
                for q in range(-1, 5)
                for r in range(-1, 2)
            ),
        )
        placements = tuple(
            Placement(actor_id=a, position=Hex(q=q, r=0), hex_facing=0 if a == "a" else 3)
            for a, q, f in (("a", 0, 0), ("b", 1, 3), ("c", 3, 3))
        )
    else:
        board = Battlefield(
            id="field",
            location_id="dock",
            width=4,
            height=1,
            blocked=(GridPoint(x=2, y=0),) if blocked else (),
        )
        placements = tuple(
            Placement(
                actor_id=a, position=GridPoint(x=q, y=0), facing="east" if a == "a" else "west"
            )
            for a, q, f in (("a", 0, "east"), ("b", 1, "west"), ("c", 3, "west"))
        )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        battlefield=board,
        placements=placements,
        aware_of=("a", "b", "c"),
        runtime_world=world().learn("a", "promise"),
    )
    if backend == "postgres":
        checkpoint = await play.store.read(cid)
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, checkpoint)
        play = build_play(tmp_path, play.engine, store=store)
    seed = play._load(await play.store.read(cid))
    encounter = seed.encounters[0]
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"movement_allowance": 11}) if p.actor_id == "c" else p
                for p in encounter.participants
            )
        }
    )
    updated = seed.model_copy(
        update={
            "encounters": (encounter,),
            "revision": seed.revision + 1,
            "resources": seed.resources.model_copy(update={"revision": seed.revision + 1}),
        }
    )

    def install(campaign: Campaign) -> CommandReceipt:
        campaign["play_json"] = updated.model_dump_json()
        campaign["revision"] = updated.revision
        return CommandReceipt(action="combat", outcome="movement-fixture")

    await play.store.commit_turn(
        cid, "movement-fixture", seed.revision, "movement-fixture", install
    )
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=updated.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    before = await play.store.read(cid)
    state = play._load(before)
    command = ChooseDefense(
        id="protect",
        actor_id="c",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        sacrificial_for="b",
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale-protect", "expected_revision": state.revision - 1}
            ),
            principal_id="c",
        )
    assert await play.store.read(cid) == before
    assert play.rng.exhausted()
    if blocked:
        play.rng = RecordedDice([])
        with pytest.raises(ValidationError, match="No legal interposition step"):
            await CombatService(play).execute(cid, command, principal_id="c")
        assert await play.store.read(cid) == before
        assert play.rng.exhausted()
    else:
        play.rng = RecordedDice([3, 3, 3, 2, 2, 2, 2])
        result = await CombatService(play).execute(cid, command, principal_id="c")
        updated = play._load(await play.store.read(cid))
        assert result.injury is not None and result.injury.injury > 0
        assert updated.encounters[0].participants[2].position == placements[1].position
        assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == next(
            p.current for p in state.resources.pools if p.id == "hp:b"
        )
        assert play.rng.exhausted()
        play.rng = RecordedDice([])
        assert await CombatService(play).execute(cid, command, principal_id="c") == result
