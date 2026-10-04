"""Actual purchased Rooted Feet casts inspect source cost, time and target state."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture

from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect, effects


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,status,cost",
    [
        ((3, 3, 3, 6, 6, 6), "active", 3),
        ((3, 3, 3, 1, 1, 1), "resisted", 3),
        ((4, 4, 4), "failed", 1),
        ((1, 1, 1), "active", 0),
        ((6, 6, 6, 3, 3, 4), "failed", 3),
    ],
)
async def test_real_rooting_or_resistance_cost_and_time(
    tmp_path: Path, backend: str, dice: tuple[int, ...], status: str, cost: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    await cast(play, cid, dice=dice)
    state = play._load(await play.store.read(cid))
    effect = effects(state.resources)["root"]
    assert effect.status == status
    assert effect.original_check.effective_target == 10
    assert state.resources.game_time == before.resources.game_time + 1
    assert effect.expires_at == state.resources.game_time + 60
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10 - cost
    assert (active_effect(state.resources, "b") is not None) == (status == "active")
    assert await play.store.read(cid) == await play.store.replay(cid)
