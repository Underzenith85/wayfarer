"""Real Haste does not change Rooted escape ST or the retained caster roll."""

import secrets
from pathlib import Path

import pytest
from support.rooted_feet import revision
from support.rooted_haste import cast_haste, cast_rooted, fixture, prepare_composition
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    ADAPTER,
    TryRootedFeetEscape,
    effects,
    escapes,
)
from wayfarer.orchestration import rooted_feet_generations
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.rooted_feet import RootedFeetService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("energy", [1, 3])
@pytest.mark.parametrize("root_generation", [1, 2])
async def test_haste_escape_retains_original_roll_and_current_st_original_reexecution(
    tmp_path: Path, backend: str, energy: int, root_generation: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, original = await fixture(tmp_path, backend)
    if root_generation == 1:
        with monkeypatch.context() as old:
            old.setattr(rooted_feet_generations, "CURRENT", 1)
            await cast_rooted(play, cid)
        await cast_haste(play, cid, energy=energy)
        recorded = await play.store.command_input(cid, "root")
        assert recorded is not None and recorded.text is not None
        from wayfarer.persistence.command_inputs import replay_payload

        payload = validation.mapping(replay_payload(recorded.text))
        assert payload["generation"] == 1
        command = ADAPTER.validate_python(payload["command"])
        before_retry = await play.store.read(cid)
        await RootedFeetService(play).execute(cid, command, principal_id="cora")
        assert await play.store.read(cid) == before_retry
        assert await play.store.command_input(cid, "root") == recorded
    else:
        await prepare_composition(play, cid, energy=energy)
    root = effects(play._load(await play.store.read(cid)).resources)["root"]
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
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "b":
        actor = state.encounters[0].current_actor_id
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="pass-" + actor,
                actor_id=actor,
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await build_runtime(play).submit_json(
        cid,
        TryRootedFeetEscape(
            id="escape",
            actor_id="b",
            expected_revision=await revision(play, cid),
            effect_id="root",
            encounter_id="fight",
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    escape_input = await play.store.command_input(cid, "escape")
    assert escape_input is not None and rooted_feet_generations.generation(escape_input) == 2
    assert escapes(state.resources)[-1].check.base_target == 5
    assert effects(state.resources)["root"].original_check == root.original_check
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 10
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
