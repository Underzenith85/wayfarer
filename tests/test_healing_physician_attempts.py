"""B248 first-attempt benefit, with real spell/caster/subject/day boundaries."""

from pathlib import Path

import pytest
from support.healing_physician import Healing, complete, fixture, reexecute, revision, start

from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.healing_effects import attempts


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["minor-healing", "major-healing"])
@pytest.mark.parametrize("boundary", ["repeat", "spell", "caster", "subject", "day"])
async def test_earlier_success_removes_only_the_matching_attempts_mitigation(
    tmp_path: Path, backend: str, spell: Healing, boundary: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "point", peer=boundary == "caster")
    play.seeds = lambda: f"{11:064x}"  # Five: ordinary success at actual12/13.
    successful = await complete(play, cid, await start(play, cid, spell, "first"))
    assert successful.outcome == "active" and successful.hp_restored == (
        2 if spell == "minor-healing" else 4
    )
    assert successful.energy_spent == 2 and successful.checks[-1].total == 5
    state = play._load(await play.store.read(cid))
    assert attempts(state.resources, spell, "a", "b") == 1
    selected: Healing = (
        ("major-healing" if spell == "minor-healing" else "minor-healing")
        if boundary == "spell"
        else spell
    )
    actor = "b" if boundary == "caster" else "a"
    suffix = "-self-b" if boundary == "caster" else "-self-a" if boundary == "subject" else ""
    subject = "a" if boundary == "subject" else "b"
    if boundary == "day":
        remaining = 86400
        step = 0
        while remaining:
            ticks = min(remaining, 10000)
            await play.execute(
                cid,
                Wait(
                    id=f"day-{step}",
                    actor_id="a",
                    ticks=ticks,
                    expected_revision=await revision(play, cid),
                ),
                principal_id="a",
            )
            remaining -= ticks
            step += 1
    before = play._load(await play.store.read(cid))
    assert attempts(before.resources, selected, actor, subject) == (
        1 if boundary == "repeat" else 0
    )
    play.seeds = lambda: f"{540:064x}"
    critical = await complete(
        play, cid, await start(play, cid, selected, "second", actor_id=actor, suffix=suffix)
    )
    assert critical.checks[-1].total == 18 and critical.hp_restored == 0
    assert critical.outcome == ("critical-failure" if boundary == "repeat" else "failed")
    assert critical.energy_spent == (2 if boundary == "repeat" else 1)
    after = play._load(await play.store.read(cid))
    assert len(backfires(after.resources)) == (1 if boundary == "repeat" else 0)
    assert attempts(after.resources, selected, actor, subject) == (2 if boundary == "repeat" else 1)
    # Printed self-healing injury penalty is applied by native casting; it never
    # changes the independent first-attempt Physician training requirement.
    assert critical.checks[-1].effective_target >= 3
    await reexecute(play, cid, tmp_path / "attempt-boundary-seed-replay")
