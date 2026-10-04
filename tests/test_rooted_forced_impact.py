"""Actual canonical subdual attack refuses unverified rooted shield knockback."""

from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import StartEncounter, TakeCombatTurn
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.rooted_feet import RootedFeetService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("rooted", [False, True])
async def test_actual_subdual_crushing_into_ready_shield_boundary(
    tmp_path: Path, backend: str, rooted: bool
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        combat_weapons=True,
        shield_rush_fixture=True,
        bidirectional_visibility=True,
    )
    await observe(play, cid, target="a")
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6) if rooted else (3, 3, 3, 1, 1, 1))
    await RootedFeetService(play).execute(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ),
        principal_id="cora",
    )
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
                Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="pass",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="a",
    )
    command = TakeCombatTurn(
        id="flat",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-b",
        mode_id="swing",
        target_id="a",
        subdual_mode="flat",
        hex_facing=3,
    )
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    if rooted:
        with pytest.raises(ValidationError, match="Rooted Feet.*shield knockback"):
            await service.execute(cid, command, principal_id="b")
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == events
        return
    opened = await service.execute(cid, command, principal_id="b")
    assert opened.code == "combat.defense_required"
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None and pending.subdual_mode == "flat"
    # Same real canonical attack is legal after a fully paid resisted cast.
    play.rng = RecordedDice((3, 3, 3, 3))
    result = await service.execute(
        cid,
        {
            "id": "defend",
            "actor_id": "a",
            "expected_revision": await revision(play, cid),
            "kind": "choose_defense",
            "encounter_id": "fight",
            "defense": "none",
        },
        principal_id="a",
    )
    assert result.injury is not None and result.injury.injury > 0
    assert await play.store.read(cid) == await play.store.replay(cid)
