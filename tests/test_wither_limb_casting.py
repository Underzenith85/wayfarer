"""Approved Wither-only learning drives real fixed-cost concentration."""

from pathlib import Path

import pytest
from support.wither_limb import cast, fixture, revision

from wayfarer.engine.simulation.magic.wither_spell_state import casts


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_wither_build_drives_actual_one_second_cast(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    purchases = {p.definition_id for p in before.actors[0].proposal.draft.purchases}
    assert "spell:wither-limb" in purchases
    assert "spell:paralyze-limb" in purchases and "spell:deathtouch" not in purchases
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    after = play._load(await play.store.read(cid))
    charge = casts(after.resources)["wither"]
    assert charge.status == "held" and charge.skill == 16 and charge.energy == 5
    assert charge.credited_seconds == 1 and charge.completed_at == before.resources.game_time + 1
    assert after.resources.game_time == before.resources.game_time + 1
    assert charge.check is not None and charge.check.outcome.succeeded
    expected = 0 if charge.check.outcome.value == "critical-success" else 4
    assert charge.paid_fp == expected
    assert (
        next(p.current for p in after.resources.pools if p.id == "fp:a")
        == next(p.current for p in before.resources.pools if p.id == "fp:a") - expected
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert not casts(play._load(original).resources)


def test_wither_construction_requires_magery_two_and_paralyze() -> None:
    from test_body_control_learning import LOWER, compile_spells

    valid = compile_spells((*LOWER, "paralyze-limb", "wither-limb"), magery=2)
    assert valid.legal and valid.build is not None
    invalid_magery = compile_spells((*LOWER, "paralyze-limb", "wither-limb"), magery=1)
    invalid_chain = compile_spells((*LOWER, "wither-limb"), magery=2)
    for invalid in (invalid_magery, invalid_chain):
        assert not invalid.legal and invalid.build is None
        assert any(
            d.code == "skill.prerequisite" and "spell:wither-limb" in d.message
            for d in invalid.diagnostics
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,expected_fp,status",
    [((6, 6, 5), 1, "failed"), ((1, 1, 1), 0, "held")],
)
async def test_actual_cast_failure_and_critical_success_pay_source_cost(
    tmp_path: Path, backend: str, dice: tuple[int, int, int], expected_fp: int, status: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
    from wayfarer.engine.simulation.magic.wither_spell_commands import CastWitherLimb
    from wayfarer.orchestration.wither_spells import WitherSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = WitherSpellService(play)
    command = CastWitherLimb(
        id="paid-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="paid",
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    await service.execute(cid, command, principal_id="alice")
    await service.execute(
        cid,
        command.model_copy(
            update={
                "id": "paid-work",
                "operation": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice(dice)
    await service.execute(
        cid,
        command.model_copy(
            update={
                "id": "paid-complete",
                "operation": "complete",
                "expected_revision": before.revision,
            }
        ),
        principal_id="alice",
    )
    charge = casts(play._load(await play.store.read(cid)).resources)["paid"]
    assert charge.status == status and charge.paid_fp == expected_fp
    assert charge.energy == 5 and charge.check is not None and charge.check.dice == dice


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_critical_failure_pays_and_records_canonical_backfire(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.backfires import backfires
    from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
    from wayfarer.engine.simulation.magic.wither_spell_commands import CastWitherLimb
    from wayfarer.orchestration.wither_spells import WitherSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = WitherSpellService(play)
    command = CastWitherLimb(
        id="critical-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="critical",
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    await service.execute(cid, command, principal_id="alice")
    await service.execute(
        cid,
        command.model_copy(
            update={
                "id": "critical-work",
                "operation": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    play.rng = RecordedDice((6, 6, 6, 3, 3, 4))
    await service.execute(
        cid,
        command.model_copy(
            update={
                "id": "critical-complete",
                "operation": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    charge = casts(state.resources)["critical"]
    assert charge.status == "failed" and charge.paid_fp == 4
    backfire = backfires(state.resources)[0]
    assert backfire.spell_id == "wither-limb" and backfire.cast_id == "critical"
    assert backfire.dice == (3, 3, 4) and backfire.row == 10 and backfire.flavor == "noise"
