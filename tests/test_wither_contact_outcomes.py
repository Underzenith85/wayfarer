"""Actual opposed Wither contact outcomes, independently of charging success."""

from pathlib import Path

import pytest
from support.wither_limb import fixture, revision
from test_wither_limb_host import prepare_contact

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.wither_spell_state import casts, contact_results
from wayfarer.orchestration.combat import ChooseDefense, CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "case",
    [
        "resisted",
        "tie",
        "contact-failed",
        "critical-success",
        "critical-failure",
        "initial-critical-resisted",
    ],
)
async def test_actual_wither_second_contact_roll_and_resistance_outcomes(
    tmp_path: Path, backend: str, case: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    initial_critical = case == "initial-critical-resisted"
    initial_rng = RecordedDice((1, 1, 1) if initial_critical else (3, 3, 3))
    play.rng = initial_rng
    await prepare_contact(play, cid)
    assert initial_rng.exhausted()
    before = play._load(await play.store.read(cid))
    charge = casts(before.resources)["wither"]
    assert charge.check is not None
    assert charge.check.dice == ((1, 1, 1) if initial_critical else (3, 3, 3))
    assert charge.paid_fp == (0 if initial_critical else 4)
    contact_dice = (
        (1, 1, 1)
        if case == "critical-success"
        else (6, 6, 6)
        if case == "critical-failure"
        else (6, 6, 5)
        if case == "contact-failed"
        else (3, 3, 3)
        if initial_critical
        else (4, 5, 5)
    )
    resistance_dice = (
        (3, 2, 2)
        if case == "resisted"
        else (2, 3, 3)
        if case == "tie"
        else (1, 1, 1)
        if initial_critical
        else ()
    )
    aftermath = (
        (1, 3, 3, 3)
        if case == "critical-success"
        else (3, 3, 4)
        if case == "critical-failure"
        else ()
    )
    rng = RecordedDice((3, 3, 3, 1) + contact_dice + resistance_dice + aftermath)
    play.rng = rng
    response = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="outcome-response",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert rng.exhausted()
    assert response.injury is not None and response.injury.injury == 1
    after = play._load(await play.store.read(cid))
    result = contact_results(after.resources)[0]
    success = case == "critical-success"
    assert result.outcome == (
        "withered"
        if success
        else "contact-failed"
        if case in ("contact-failed", "critical-failure")
        else "resisted"
    )
    assert result.triggered and result.status == "spent"
    assert result.contact_check is not None and result.contact_check.dice == contact_dice
    if resistance_dice:
        assert result.resistance_check is not None
        assert result.resistance_check.dice == resistance_dice
        assert result.resistance_check.base_target == 10
        if case == "tie":
            assert result.contact_check.margin == result.resistance_check.margin == 2
            assert result.contest_winner is None
    else:
        assert result.resistance_check is None
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.injury is not None
    assert result.hp_before == 9 and result.hp_after == hp.current == (8 if success else 9)
    assert result.dice == ((1,) if success else ()) and result.injury == int(success)
    assert ("right-arm" in disabled(after.resources, "b")) == success
    assert (result.lasting_id is not None) == success
    assert result.injury_check_reasons == (("major-wound",) if success else ())
    if success:
        permanent = next(w for w in hp.injury.lasting_injuries if w.id == result.lasting_id)
        assert permanent.kind == "crippled" and permanent.duration == "permanent"
        assert permanent.recovery_at is None and len(result.injury_checks) == 1
    else:
        assert not hp.injury.lasting_injuries and not result.injury_checks
    spent = casts(after.resources)["wither"]
    assert spent.status == "spent" and spent.paid_fp == charge.paid_fp
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == next(
        p.current for p in before.resources.pools if p.id == "fp:a"
    )
    recorded_backfires = backfires(after.resources)
    if case == "critical-failure":
        assert len(recorded_backfires) == 1
        assert recorded_backfires[0].spell_id == "wither-limb"
        assert recorded_backfires[0].dice == (3, 3, 4) and recorded_backfires[0].flavor == "noise"
    else:
        assert not recorded_backfires
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_held_wither_refuses_registered_other_spell_start_before_rng(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_runtime
    from support.wither_limb import cast

    from wayfarer.engine.simulation.magic.limb_spell_commands import CastParalyzeLimb
    from wayfarer.errors import ConflictError

    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    held = await cast(play, cid)
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    assert casts(play._load(before).resources)["wither"].status == "held"
    rng = RecordedDice(())
    play.rng = rng
    other = CastParalyzeLimb(
        id="blocked-other-spell",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="other-spell",
        carrier=held.carrier,
    )
    with pytest.raises(ConflictError, match="held Melee spell"):
        await build_runtime(play).submit_json(
            cid, other.model_dump(mode="json"), principal_id="alice"
        )
    assert rng.exhausted()
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
