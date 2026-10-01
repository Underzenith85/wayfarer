"""Staff observations across canonical movement and seeded command reexecution."""

from pathlib import Path

import pytest
from support.runtime import played
from test_staff_casting import prepare
from test_tactical import migration

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.staff_casting import touching
from wayfarer.engine.simulation.magic.staff_casting_state import (
    ObserveStaffTouch,
    intents,
    invalidated,
)
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.staff_casting import StaffCastingService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_host_reexecutes_recorded_commands(tmp_path: Path, backend: str) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=1, pointing=False, seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="touch", actor_id="a", expected_revision=2, cast_id="cast"),
        principal_id="gm",
    )
    await SpellService(play).execute(
        cid, command.model_copy(update={"expected_revision": 3}), principal_id="a"
    )
    await play.execute(
        cid, Wait(id="one-second", actor_id="a", expected_revision=4, ticks=1), principal_id="a"
    )
    await SpellService(play).execute(
        cid,
        command.model_copy(update={"id": "complete", "kind": "complete", "expected_revision": 5}),
        principal_id="a",
    )
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_hex_loop_expires_touch_before_cast_roll(tmp_path: Path, backend: str) -> None:
    cid, play, command = await prepare(
        tmp_path, backend, combat=True, distance=1, pointing=False, spell="daze"
    )
    combat = CombatService(play)
    migrate = migration()
    await combat.execute(
        cid,
        migrate.model_copy(
            update={
                "expected_revision": 3,
                "placements": migrate.placements[:2],
                "battlefield": migrate.battlefield.model_copy(update={"id": "forge"}),
            }
        ),
        principal_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="touch", actor_id="a", expected_revision=4, cast_id="cast"),
        principal_id="gm",
    )
    await SpellService(play).execute(
        cid, command.model_copy(update={"expected_revision": 5}), principal_id="a"
    )
    before = play._load(await play.store.read(cid))
    assert touching(play.rules_context, before, intents(before.resources)[0])
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="target-loop",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            maneuver="move",
            hex_path=(Hex(q=2, r=0), Hex(q=1, r=0)),
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    assert (
        after.encounters[0].participants[1].position
        == before.encounters[0].participants[1].position
    )
    assert "touch" in invalidated(after.resources)
    assert not touching(play.rules_context, after, intents(after.resources)[0])
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "finish", "kind": "concentrate", "expected_revision": after.revision}
        ),
        principal_id="a",
    )
    assert result.checks[0].effective_target == 13
    assert result.energy_spent == 3
    assert await play.store.read(cid) == await play.store.replay(cid)
