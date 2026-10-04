"""Paid B244 Rooted Feet stops physical relocation before canonical side effects."""

from pathlib import Path
from typing import Literal, NoReturn

import pytest
from support.rooted_feet import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Move, Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalService
from wayfarer.orchestration.scenes import SceneService, TravelScene


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_rooting_blocks_world_and_registered_scene_travel_atomically(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    result = await play.execute(
        cid,
        Move(
            id="walk",
            actor_id="b",
            expected_revision=await revision(play, cid),
            destination_id="alley",
        ),
        principal_id="b",
    )
    assert result.code == "move.rooted"
    state = play._load(await play.store.read(cid))
    original = play._load(before)
    assert state.world == original.world
    assert state.resources.game_time == original.resources.game_time
    assert state.resources.pools == original.resources.pools
    snapshot = await play.store.read(cid)
    with pytest.raises(ConflictError, match="Rooted Feet"):
        await SceneService(play).execute(
            cid,
            TravelScene(
                id="exit", actor_id="b", expected_revision=state.revision, exit_id="to-alley"
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == snapshot


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "maneuver,timing", [("do_nothing", "before"), ("do_nothing", "after"), ("move", "before")]
)
async def test_paid_rooting_blocks_combat_step_but_allows_in_place_turn(
    tmp_path: Path,
    backend: str,
    maneuver: Literal["do_nothing", "move"],
    timing: Literal["before", "after"],
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=4, y=4)),
                Placement(actor_id="b", position=GridPoint(x=1, y=1)),
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    if state.encounters[0].current_actor_id == "a":
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="a-turn",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="a",
        )
    before = await play.store.read(cid)
    state = play._load(before)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Rooted Feet"):
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="step",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver=maneuver,
                step_timing=timing,
                destination=GridPoint(x=1, y=2),
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="still",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    assert after.encounters[0].participants[1].position == GridPoint(x=1, y=1)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["climb", "jump", "hike", "swim"])
async def test_paid_rooting_refuses_physical_task_before_route_or_dice(
    tmp_path: Path, backend: str, kind: Literal["climb", "jump", "hike", "swim"]
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    before = await play.store.read(cid)

    def resolver(*args: object) -> NoReturn:
        pytest.fail("Rooted actor reached physical route resolution")

    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Rooted Feet"):
        await PhysicalService(play, resolver).execute(
            cid,
            PhysicalCommand(
                kind=kind,
                id="physical-feat",
                actor_id="b",
                expected_revision=await revision(play, cid),
                route_id="route",
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_rooting_movement_restores_at_real_clock_deadline(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    state = play._load(await play.store.read(cid))
    assert movement(play.rules_context, state, "b") == 0
    await play.execute(
        cid,
        Wait(id="wait59", actor_id="b", expected_revision=state.revision, ticks=59),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert movement(play.rules_context, state, "b") == 0
    await play.execute(
        cid,
        Wait(id="wait1", actor_id="b", expected_revision=state.revision, ticks=1),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert movement(play.rules_context, state, "b") > 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_rooting_refuses_actual_pending_attack_retreat_before_defense_dice(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_maneuvers import defend, turn

    cid, play, _ = await fixture(tmp_path, backend, combat_weapons=True)
    await cast(play, cid)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice((3, 3, 3))
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Rooted Feet"):
        await defend(cid, play, "b", "dodge", basic_retreat=True)
    assert await play.store.read(cid) == before
    assert play.rng.exhausted()
