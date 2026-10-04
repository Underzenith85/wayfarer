"""Actual B249 Regular outcomes through current manufacture and spell producers."""

import json
from pathlib import Path

import pytest
from support.detect_magic import complete, fixture, start, work

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.simulation.magic.detect_magic_state import findings


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("carrier", ["permanent", "temporary", "mundane"])
@pytest.mark.parametrize(
    "seed,outcome,cost",
    [
        (1, Outcome.SUCCESS, 2),
        (48, Outcome.FAILURE, 1),
        (30, Outcome.CRITICAL_FAILURE, 2),
        (14, Outcome.CRITICAL_SUCCESS, 0),
    ],
)
async def test_regular_cost_real_material_truth_and_backfire(
    tmp_path: Path,
    backend: str,
    carrier: str,
    seed: int,
    outcome: Outcome,
    cost: int,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, carrier)
    actor = "c"
    before = play._load(await play.store.read(cid))
    fp = next(p.current for p in before.resources.pools if p.id == "fp:" + actor)
    hp = next(p.current for p in before.resources.pools if p.id == "hp:" + actor)
    await start(play, cid, carrier=carrier)
    await work(play, cid, actor=actor)
    result = await complete(play, cid, seed, actor=actor)
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == before.resources.game_time + 5
    assert result.check is not None and result.check.outcome == outcome
    assert result.energy_spent == cost
    assert next(p.current for p in state.resources.pools if p.id == "fp:" + actor) == fp - cost
    patient = next(p for p in state.resources.pools if p.id == "hp:" + actor)
    assert patient.current == hp - int(outcome == Outcome.CRITICAL_FAILURE)
    if outcome == Outcome.CRITICAL_FAILURE:
        assert patient.injury is not None and patient.injury.shock == 1
    if outcome.succeeded:
        assert result.finding is not None
        assert result.finding.magical is (carrier != "mundane")
        critical = outcome == Outcome.CRITICAL_SUCCESS
        assert result.finding.permanence == (
            ("temporary" if carrier == "temporary" else "permanent")
            if critical and carrier != "mundane"
            else None
        )
        assert (
            result.finding.spell_id == ("magelock" if carrier == "temporary" else "haste")
            if critical and carrier != "mundane"
            else result.finding.spell_id is None
        )
        assert result.finding.power == (22 if critical and carrier == "permanent" else None)
    else:
        assert result.finding is None and not findings(state.resources)
    public = json.dumps(result.model_dump(mode="json"))
    assert "identity" not in public and "binding_id" not in public and "project_id" not in public
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("carrier", ["permanent", "temporary"])
@pytest.mark.parametrize("second_seed", [1, 14])
async def test_second_success_classifies_current_subject_without_daily_information_gate(
    tmp_path: Path,
    backend: str,
    carrier: str,
    second_seed: int,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, carrier)
    actor = "c"
    await start(play, cid, carrier=carrier)
    await work(play, cid, actor=actor)
    first = await complete(play, cid, 1, actor=actor)
    assert first.finding is not None and first.finding.permanence is None
    await start(play, cid, carrier=carrier, name="second")
    await work(play, cid, actor=actor, name="second")
    second = await complete(play, cid, second_seed, actor=actor, name="second")
    assert second.finding is not None
    assert second.finding.permanence == ("temporary" if carrier == "temporary" else "permanent")
    assert (
        second.finding.spell_id == ("magelock" if carrier == "temporary" else "haste")
        if second_seed == 14
        else second.finding.spell_id is None
    )
