"""Actual critical-item manufacture to source-valid secret Information discovery."""

import json
from pathlib import Path

import pytest
from support.analyze_magic import complete, fixture, report, start, work
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    discovery_records,
    knows_power,
    projection,
    secret_result,
)
from wayfarer.orchestration.analyze_magic import AnalyzeMagicService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "seed,claimed,truth", [(1, None, True), (48, None, False), (30, 24, False)]
)
async def test_real_hour_secret_cost_truth_or_false_belief_and_seed(
    tmp_path: Path, backend: str, seed: int, claimed: int | None, truth: bool
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    binding = next(i for i in state.resources.items if i.id == "cloak").enchantments[0]
    assert binding.power == 22 and not knows_power(state.resources, "c", binding)
    beginning = state.resources.game_time
    await start(play, cid)
    await work(play, cid)
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == beginning + 3600
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10
    command = await complete(play, cid, seed)
    state = play._load(await play.store.read(cid))
    secret = secret_result(state.resources, "analysis")
    assert secret is not None and secret.check.effective_target == 12
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 2
    response = await AnalyzeMagicService(play).execute(cid, command, principal_id="cora")
    assert response.model_dump(mode="json") == {"command_id": command.id, "outcome": "finished"}
    for principal in ("cora", "alice", "bob", "watcher"):
        for name in ("campaign", "stream"):
            view = await build_runtime(play).project(name, cid, principal_id=principal)
            assert "analyze-magic:" not in json.dumps(view)
            assert "check" not in json.dumps(view.get("magic_findings", ()))
            assert not projection(state.resources, ("c",))
    await report(play, cid, claimed)
    final = await play.store.read(cid)
    state = play._load(final)
    assert knows_power(state.resources, "c", binding) is truth
    assert bool(discovery_records(state.resources)) is truth
    visible_power = 22 if truth else claimed
    runtime = build_runtime(play)
    for name in ("campaign", "stream"):
        view = await runtime.project(name, cid, principal_id="cora")
        findings = validation.sequence(validation.decode(json.dumps(view["magic_findings"])))
        assert findings == [
            {"actor_id": "c", "item_id": "cloak", "spell_id": "haste", "power": visible_power}
        ]
        items = validation.sequence(validation.decode(json.dumps(view["inventory"])))
        item = next(validation.mapping(i) for i in items if validation.mapping(i)["id"] == "cloak")
        shown = validation.mapping(validation.sequence(item["enchantments"])[0])
        if visible_power is None:
            assert "power" not in shown
        else:
            assert shown["power"] == visible_power
        assert "truthful" not in json.dumps(view) and "actual_power" not in json.dumps(view)
        for principal in ("alice", "bob", "watcher"):
            other = await runtime.project(name, cid, principal_id=principal)
            assert "magic_findings" not in other
    assert next(i for i in state.resources.items if i.id == "cloak").enchantments[0].power == 22
    assert final == await play.store.replay(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
