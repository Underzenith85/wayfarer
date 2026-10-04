"""Approved Paralyze-only learning drives real fixed-cost concentration."""

from pathlib import Path

import pytest
from support.paralyze_limb import cast, fixture, revision

from wayfarer.engine.simulation.magic.limb_spell_state import casts


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_paralyze_build_drives_actual_one_second_cast(
    tmp_path: Path, backend: str
) -> None:
    cid, play, original = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    purchases = {p.definition_id for p in before.actors[0].proposal.draft.purchases}
    assert "spell:paralyze-limb" in purchases
    assert "spell:wither-limb" not in purchases and "spell:deathtouch" not in purchases
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    after = play._load(await play.store.read(cid))
    charge = casts(after.resources)["paralyze"]
    assert charge.status == "held" and charge.skill == 16 and charge.energy == 3
    assert charge.credited_seconds == 1 and charge.completed_at == before.resources.game_time + 1
    assert after.resources.game_time == before.resources.game_time + 1
    assert charge.check is not None and charge.check.outcome.succeeded
    expected = 0 if charge.check.outcome.value == "critical-success" else 2
    assert charge.paid_fp == expected
    assert (
        next(p.current for p in after.resources.pools if p.id == "fp:a")
        == next(p.current for p in before.resources.pools if p.id == "fp:a") - expected
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert not casts(play._load(original).resources)


def test_paralyze_construction_requires_pain_and_five_lawful_other_spells() -> None:
    from test_body_control_learning import LOWER, compile_spells

    valid = compile_spells((*LOWER, "paralyze-limb"), magery=1)
    assert valid.legal and valid.build is not None
    assert next(v.value for v in valid.build.sheet.values if v.target == "spell:paralyze-limb") == 9
    invalid = compile_spells(
        ("itch", "spasm", "clumsiness", "hinder", "rooted-feet", "paralyze-limb"), magery=1
    )
    assert not invalid.legal and invalid.build is None
    assert any(d.code == "spell.prerequisite" for d in invalid.diagnostics)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_registered_paralyze_restarts_exact_retry_and_reexecutes_original_genesis(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_runtime

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play, original = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    command = await cast(play, cid)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    restarted = play.for_campaign(before)
    await build_runtime(restarted).submit_json(
        cid, command.model_dump(mode="json"), principal_id="alice"
    )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    records = history[1:]
    assert history[0].command_id == "setup:seed"
    assert all(row.reexecutable for row in records)
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        original,
        records,
        [event for event in stream if event.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert replayed == before and len(checks) == len(records)
    assert all(check.folded and check.reexecuted for check in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_paralyze_authority_stale_and_candidate_rollback(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.limb_spell_commands import CastParalyzeLimb
    from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
    from wayfarer.errors import AuthorizationError, ConflictError
    from wayfarer.orchestration.limb_spells import LimbSpellService
    from wayfarer.orchestration.pipeline import submit

    cid, play, _ = await fixture(tmp_path, backend)
    command = CastParalyzeLimb(
        id="guard-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="guarded",
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await LimbSpellService(play).execute(cid, command, principal_id="bob")
    with pytest.raises(ConflictError):
        await LimbSpellService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="alice",
        )
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    await LimbSpellService(play).execute(cid, command, principal_id="alice")
    await LimbSpellService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "guard-work",
                "operation": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    completion = command.model_copy(
        update={
            "id": "guard-complete",
            "operation": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            LimbSpellService(failing).plan(
                failing, failing._load(before), completion, principal_id="alice"
            ),
            principal_id="alice",
        )
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    assert casts(play._load(before).resources)["guarded"].status == "casting"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,expected_fp,status",
    [((6, 6, 5), 1, "failed"), ((1, 1, 1), 0, "held")],
)
async def test_actual_cast_failure_and_critical_success_pay_source_cost(
    tmp_path: Path, backend: str, dice: tuple[int, int, int], expected_fp: int, status: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.limb_spell_commands import CastParalyzeLimb
    from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
    from wayfarer.orchestration.limb_spells import LimbSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = LimbSpellService(play)
    command = CastParalyzeLimb(
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
    assert charge.energy == 3 and charge.check is not None and charge.check.dice == dice


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_critical_failure_pays_and_records_canonical_backfire(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.backfires import backfires
    from wayfarer.engine.simulation.magic.limb_spell_commands import CastParalyzeLimb
    from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
    from wayfarer.orchestration.limb_spells import LimbSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = LimbSpellService(play)
    command = CastParalyzeLimb(
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
    assert charge.status == "failed" and charge.paid_fp == 2
    backfire = backfires(state.resources)[0]
    assert backfire.spell_id == "paralyze-limb" and backfire.cast_id == "critical"
    assert backfire.dice == (3, 3, 4) and backfire.row == 10 and backfire.flavor == "noise"
