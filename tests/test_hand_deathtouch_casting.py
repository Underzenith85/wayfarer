"""Registered selected-hand producer outcomes; physical contact proof is separate."""

from pathlib import Path

import pytest
from support.hand_deathtouch import fixture, revision
from support.runtime import build_runtime

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.hand_melee_spell_state import CastHandDeathtouch, casts
from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier
from wayfarer.errors import ConflictError
from wayfarer.orchestration.hand_melee_spells import HandMeleeSpellService
from wayfarer.orchestration.play import PlayService


async def submit(
    play: PlayService, cid: str, operation: str, *, identity: str | None = None
) -> dict[str, object]:
    command = CastHandDeathtouch.model_validate(
        {
            "id": identity or "hand-" + operation,
            "actor_id": "a",
            "expected_revision": await revision(play, cid),
            "operation": operation,
            "cast_id": "hand",
            "energy": 3,
            "carrier": HandCarrier(hand="right-hand"),
        }
    )
    return await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "roll,status,paid",
    [((6, 6, 5), "failed", 1), ((1, 1, 1), "held", 0), ((6, 6, 6, 3, 3, 4), "failed", 2)],
)
async def test_registered_failure_and_critical_outcomes_preserve_energy(
    tmp_path: Path, backend: str, roll: tuple[int, ...], status: str, paid: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    initial = play._load(await play.store.read(cid))
    await submit(play, cid, "start")
    await submit(play, cid, "concentrate")
    play.rng = RecordedDice(roll)
    result = await submit(play, cid, "complete")
    state = play._load(await play.store.read(cid))
    charge = casts(state.resources)["hand"]
    assert charge.status == status and charge.energy == 3 and charge.paid_fp == paid
    assert state.resources.game_time == initial.resources.game_time + 1
    assert charge.credited_seconds == 1 and charge.check is not None
    if roll[:3] == (6, 6, 6):
        backlash = backfires(state.resources)
        assert len(backlash) == 1
        assert backlash[0].cast_id == "hand" and backlash[0].spell_id == "deathtouch"
        assert backlash[0].dice == (3, 3, 4) and backlash[0].row == 10
        assert backlash[0].severity == "normal" and backlash[0].flavor == "noise"
    else:
        assert not backfires(state.resources)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10 - paid
    assert play.rng.exhausted()
    saved, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    command = CastHandDeathtouch(
        id="hand-complete",
        actor_id="a",
        expected_revision=initial.revision + 2,
        operation="complete",
        cast_id="hand",
        energy=3,
        carrier=HandCarrier(hand="right-hand"),
    )
    repeated = await HandMeleeSpellService(play).execute(cid, command, principal_id="alice")
    assert repeated.outcome == status and repeated.energy_spent == paid
    assert (
        await build_runtime(play).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
        == result
    )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == history and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill,paid", [(14, 3), (15, 2)])
async def test_actual_skill_fifteen_discount_boundary(
    tmp_path: Path, backend: str, skill: int, paid: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, spell_skill=skill)
    await submit(play, cid, "start")
    await submit(play, cid, "concentrate")
    play.rng = RecordedDice((3, 3, 3))
    await submit(play, cid, "complete")
    state = play._load(await play.store.read(cid))
    charge = casts(state.resources)["hand"]
    assert charge.skill == skill and charge.energy == 3 and charge.paid_fp == paid
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10 - paid
    assert charge.status == "held" and play.rng.exhausted()
    play.rng = RecordedDice(())
    await submit(play, cid, "cancel")
    assert casts(play._load(await play.store.read(cid)).resources)["hand"].status == "cancelled"
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_wait_interrupts_uncredited_hand_concentration(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await submit(play, cid, "start")
    play.rng = RecordedDice(())
    await build_runtime(play).submit_json(
        cid,
        Wait(
            id="outside-wait", actor_id="a", expected_revision=await revision(play, cid), ticks=1
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    charge = casts(state.resources)["hand"]
    assert charge.status == "cancelled" and charge.credited_seconds == 0
    assert charge.check is None and charge.paid_fp == 0
    saved, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    with pytest.raises(ConflictError, match="continuation"):
        await submit(play, cid, "complete")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
