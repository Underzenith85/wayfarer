"""Actual Create Water modifies the canonical inventory carrier and mass."""

from pathlib import Path

import pytest
from support.water_inventory import begin, complete, declare, fixture

from wayfarer.engine.simulation.magic.water_inventory import materials
from wayfarer.engine.simulation.magic.water_state import latest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("success", [True, False])
async def test_actual_create_wineskin_quantity_purity_cost_time_and_mass(
    tmp_path: Path, backend: str, success: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await declare(play, cid)
    before = play._load(await play.store.read(cid))
    weight = play.engine.resources.carried_weight(before.resources, "a")
    start = await begin(play, cid)
    command, result = await complete(play, cid, start, success=success)
    after = play._load(await play.store.read(cid))
    assert result.outcome == ("active" if success else "failed") and result.energy_spent == (
        2 if success else 1
    )
    assert after.resources.game_time == before.resources.game_time + 1
    liquid = materials(after.resources)
    assert len(liquid) == int(success)
    if success:
        assert (liquid[0].gallons, liquid[0].pure_gallons, liquid[0].mass_millipounds) == (
            1,
            1,
            8000,
        )
        assert liquid[0].item_id == "wine-a" and liquid[0].command_id == command.id
    assert play.engine.resources.carried_weight(after.resources, "a") == weight + (
        8000 if success else 0
    )
    assert not latest(after.resources) and not any(e.id == "wine-a" for e in after.world.entities)
    assert await play.store.read(cid) == await play.store.replay(cid)
