"""Real Staff manufacture then source-cost, one-second personal casting."""

from pathlib import Path

import pytest
from support.melee_spell import cast, fixture, revision

from wayfarer.engine.simulation.magic.melee_spell_state import casts


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("energy", [1, 2, 3])
async def test_actual_manufactured_staff_carries_source_paid_cast(
    tmp_path: Path, backend: str, energy: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid, energy=energy)
    state = play._load(await play.store.read(cid))
    charge = casts(state.resources)["death"]
    assert charge.status == "held" and charge.energy == energy
    assert charge.skill == 16 and charge.credited_seconds == 1
    assert state.resources.game_time == before.resources.game_time + 1
    assert charge.check is not None and charge.check.outcome.succeeded
    expected = 0 if charge.check.outcome.value == "critical-success" else max(0, energy - 1)
    assert charge.paid_fp == expected
    assert (
        next(p.current for p in state.resources.pools if p.id == "fp:a")
        == next(p.current for p in before.resources.pools if p.id == "fp:a") - expected
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("problem", ["mundane", "hand", "no-work", "wrong-carrier"])
async def test_actual_invalid_carrier_or_uncredited_completion_is_atomic(
    tmp_path: Path, backend: str, problem: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.melee_spell_state import (
        CastDeathtouch,
        HandCarrier,
        StaffCarrier,
    )
    from wayfarer.errors import ConflictError
    from wayfarer.orchestration.melee_spells import MeleeSpellService

    cid, play, _ = await fixture(tmp_path, backend, manufacture=problem != "mundane")
    service = MeleeSpellService(play)
    command = CastDeathtouch(
        id="invalid-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="invalid",
        energy=3,
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    if problem == "no-work":
        await service.execute(cid, command, principal_id="alice")
        command = command.model_copy(
            update={
                "id": "invalid-complete",
                "operation": "complete",
                "expected_revision": await revision(play, cid),
            }
        )
    elif problem == "hand":
        command = command.model_copy(update={"carrier": HandCarrier(hand="right-hand")})
    elif problem == "wrong-carrier":
        command = command.model_copy(
            update={"carrier": StaffCarrier(hand="right-hand", item_id="workshop")}
        )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await service.execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,expected_fp,status",
    [((3, 3, 3), 2, "held"), ((6, 6, 5), 1, "failed"), ((1, 1, 1), 0, "held")],
)
async def test_actual_cast_failure_and_critical_success_pay_source_cost(
    tmp_path: Path, backend: str, dice: tuple[int, int, int], expected_fp: int, status: str
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.melee_spell_state import CastDeathtouch, StaffCarrier
    from wayfarer.orchestration.melee_spells import MeleeSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = MeleeSpellService(play)
    command = CastDeathtouch(
        id="paid-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="paid",
        energy=3,
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
    from wayfarer.engine.simulation.magic.melee_spell_state import CastDeathtouch, StaffCarrier
    from wayfarer.orchestration.melee_spells import MeleeSpellService

    cid, play, _ = await fixture(tmp_path, backend)
    service = MeleeSpellService(play)
    command = CastDeathtouch(
        id="critical-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="critical",
        energy=3,
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
    assert backfire.spell_id == "deathtouch" and backfire.cast_id == "critical"
    assert backfire.dice == (3, 3, 4) and backfire.row == 10 and backfire.flavor == "noise"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_registered_staff_cast_restarts_retries_and_reexecutes_original_genesis(
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
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        original,
        records,
        [e for e in stream if e.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert replayed == before and len(checks) == len(records)
    assert all(check.folded and check.reexecuted for check in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("implement", ["staff", "shield"])
async def test_defender_equipment_is_source_authored_before_staff_manufacture(
    tmp_path: Path, backend: str, implement: str
) -> None:
    cid, play, original = await fixture(
        tmp_path, backend, defender_item="staff" if implement == "staff" else "shield"
    )
    original_state = play._load(original)
    item = next(i for i in original_state.resources.items if i.id == "defender-implement")
    assert item.owner_id == "b" and item.equipped and item.ready and not item.enchantments
    assert item.definition_id == (
        "equipment:quarterstaff" if implement == "staff" else "equipment:medium-shield"
    )
    assert next(a for a in original_state.actors if a.actor_id == "b").held_item_hands
    assert (
        next(
            i
            for i in play._load(await play.store.read(cid)).resources.items
            if i.id == "defender-implement"
        )
        == item
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_readiness_transition_retains_charge_but_drop_does_not(
    tmp_path: Path, backend: str
) -> None:
    """Engine checkpoint diagnostic; no fabricated inventory command receipt."""
    from wayfarer.engine.simulation.magic.melee_spell_transitions import checkpoint

    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    state = play._load(await play.store.read(cid))
    unready = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False}) if i.id == "real-staff" else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    checked = checkpoint(unready, before=state)
    assert casts(checked.resources)["death"].status == "held"
    restored = checked.model_copy(
        update={"resources": checked.resources.model_copy(update={"items": state.resources.items})}
    )
    assert casts(checkpoint(restored, before=checked).resources)["death"].status == "held"
    dropped = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"equipped": False}) if i.id == "real-staff" else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    lost = checkpoint(dropped, before=state)
    assert casts(lost.resources)["death"].status == "dissipated"
    retrieved = lost.model_copy(
        update={"resources": lost.resources.model_copy(update={"items": state.resources.items})}
    )
    assert casts(checkpoint(retrieved, before=lost).resources)["death"].status == "dissipated"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_cast_authority_stale_and_candidate_rollback(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.melee_spell_state import CastDeathtouch, StaffCarrier
    from wayfarer.errors import AuthorizationError, ConflictError
    from wayfarer.orchestration.melee_spells import MeleeSpellService
    from wayfarer.orchestration.pipeline import submit

    cid, play, _ = await fixture(tmp_path, backend)
    command = CastDeathtouch(
        id="guard-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        operation="start",
        cast_id="guarded",
        energy=3,
        carrier=StaffCarrier(hand="right-hand", item_id="real-staff"),
    )
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await MeleeSpellService(play).execute(cid, command, principal_id="bob")
    with pytest.raises(ConflictError):
        await MeleeSpellService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="alice",
        )
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    await MeleeSpellService(play).execute(cid, command, principal_id="alice")
    await MeleeSpellService(play).execute(
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
            MeleeSpellService(failing).plan(
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
async def test_unknown_mana_location_retains_touch_but_refuses_productive_contact(
    tmp_path: Path, backend: str
) -> None:
    """Canonical location-transition diagnostic, not a fabricated travel receipt."""
    from wayfarer.engine.simulation.magic.melee_spell_transitions import checkpoint
    from wayfarer.engine.simulation.magic.melee_staff_carrier import carrier_digest
    from wayfarer.errors import ConflictError

    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    state = play._load(await play.store.read(cid))
    from dataclasses import replace

    moved = state.model_copy(
        update={
            "world": replace(
                state.world,
                entities=tuple(
                    replace(e, location_id="unobserved-scene") if e.id == "a" else e
                    for e in state.world.entities
                ),
            )
        }
    )
    checked = checkpoint(moved, before=state)
    charge = casts(checked.resources)["death"]
    assert charge.status == "held"
    with pytest.raises(ConflictError, match="authenticated normal mana"):
        carrier_digest(checked, "a", charge.carrier, require_action=True)
    assert carrier_digest(checked, "a", charge.carrier) == charge.carrier_digest
