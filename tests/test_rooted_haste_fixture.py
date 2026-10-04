"""Independent paid personal Haste producer and original-genesis replay fixture."""

from pathlib import Path

import pytest
from support.rooted_haste import cast_haste, fixture
from support.runtime import played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.magic.haste_effects import bonus
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("energy", [1, 2, 3])
async def test_genuine_paid_haste_has_current_bonus_cost_time_and_original_reexecution(
    tmp_path: Path, backend: str, energy: int
) -> None:
    cid, play, original = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    compiled = build(play.rules_context, before, "a")
    skill = next(int(v.value) for v in compiled.sheet.values if v.target == "spell:haste")
    assert skill == 18
    fp_before = next(p.current for p in before.resources.pools if p.id == "fp:a")
    await cast_haste(play, cid, energy=energy)
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["haste"]
    assert effect.actor_id == "a" and effect.target_id == "b" and effect.phase == "active"
    assert effect.energy == energy and bonus(state.resources, "b") == energy
    assert state.resources.game_time == before.resources.game_time + 2
    assert effect.expires_at == state.resources.game_time + 60
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == fp_before - (
        2 * energy - 1
    )
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == next(
        p.current for p in before.resources.pools if p.id == "fp:b"
    )
    saved = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        original,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
