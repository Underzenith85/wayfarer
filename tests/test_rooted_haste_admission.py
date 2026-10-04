"""Actual paid personal Haste and captured Rooted admission, both stores."""

from pathlib import Path

import pytest
from support.rooted_haste import cast_haste, fixture, prepare_composition
from support.runtime import played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.rooted_feet_policy import (
    qualified_haste_bonus,
    rooted_generation,
)
from wayfarer.engine.simulation.magic.rooted_feet_state import effects
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ConflictError
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", ["before", "after"])
@pytest.mark.parametrize("energy", [1, 2, 3])
async def test_actual_personal_haste_rooted_original_genesis(
    tmp_path: Path, backend: str, order: str, energy: int
) -> None:
    from typing import Literal, cast

    cid, play, original = await fixture(tmp_path, backend)
    await prepare_composition(
        play, cid, energy=energy, order=cast(Literal["before", "after"], order)
    )
    state = play._load(await play.store.read(cid))
    assert qualified_haste_bonus(state, "b") == energy
    root = effects(state.resources)["root"]
    haste = latest(state.resources)["haste"]
    assert root.status == "active"
    assert root.expires_at != haste.expires_at
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


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_rooted_policy_refuses_actual_haste_without_rng(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast_haste(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    # Diagnostic captured-policy probe uses the real producer state, not forged effects.
    with rooted_generation(1), pytest.raises(ConflictError, match="active spell"):
        from wayfarer.engine.simulation.magic.rooted_feet_admission import target_strength

        target_strength(play.rules_context, play._load(saved), "b")
    assert play.rng.exhausted() and await play.store.read(cid) == saved
