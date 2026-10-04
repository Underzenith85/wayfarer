"""Actual B481 critical manufacture keeps player item receipts epistemically private."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played
from test_critical_item_power_privacy import prepare
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_haste_manufacture import begin_project, paid_cast
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.magic.spell_state import SpellResult, event_id, parse_event
from wayfarer.engine.simulation.resources import Transfer
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.size_forms import SizeFormService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("critical", [True, False])
async def test_actual_item_receipt_retry_transfer_restart_seed(
    tmp_path: Path, backend: str, critical: bool
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    play.rng, play.seeds = secrets, lambda: f"{270 if critical else 1:064x}"
    await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    play.seeds = lambda: f"{1:064x}"
    await paid_cast(play, cid, 1)
    records = await played(play.store, cid)
    record = next(r for r in records if r.command_id == "complete")
    assert record.command_input is not None
    command = json.loads(record.command_input)["command"]
    before = await play.store.read(cid)
    state = play._load(before)
    canonical = parse_event(
        next(e for e in state.resources.events if e.id == event_id("complete", "haste"))
    ).result
    assert canonical.checks
    receipt = await HasteService(play).execute(cid, command, principal_id="alice")
    assert isinstance(receipt, SpellResult)
    assert receipt.checks == (() if critical else canonical.checks)
    assert receipt.outcome == canonical.outcome and receipt.energy_spent == canonical.energy_spent
    assert before == await play.store.read(cid)
    for principal in ("bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await HasteService(play).execute(cid, command, principal_id=principal)
    for operation in ("drop", "retrieve"):
        current = play._load(await play.store.read(cid))
        await SizeFormService(play).retrieve(
            cid,
            WorldGroundCommand(
                id="cloak-" + operation,
                actor_id="a",
                expected_revision=current.resources.revision,
                kind=operation,
                item_id="cloak",
            ),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    transfer = PartyCommand(
        id="transfer-after-use",
        actor_id="a",
        expected_revision=state.revision,
        kind="transfer_item",
        activity_json=Transfer(
            id="inside-transfer",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="cloak",
            quantity=1,
            owner_id="b",
        ).model_dump_json(),
    )
    await build_runtime(play).submit_json(
        cid, transfer.model_dump(mode="json"), principal_id="alice"
    )
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await HasteService(restarted).execute(cid, command, principal_id="alice") == receipt
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("principal", ["alice", "gm"])
@pytest.mark.parametrize("failure", [False, True])
async def test_actual_critical_receipt_audience_failure_and_seed(
    tmp_path: Path, backend: str, principal: str, failure: bool
) -> None:
    from test_haste_manufacture import revision

    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand

    cid, play, initial = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    play.rng, play.seeds = secrets, lambda: f"{270:064x}"
    await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(
                id="paid-channel",
                actor_id="a",
                target_id="a",
                location_id="dock",
                magic_item_id="cloak",
            ),
        ),
        principal_id="gm",
    )
    command = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="haste",
        cast_id="paid",
        channel_id="paid-channel",
        energy=1,
    )
    play.seeds = lambda: f"{1:064x}"
    await service.execute(cid, command, principal_id=principal)
    for identifier, kind in (("concentrate", "concentrate"), ("complete", "complete")):
        await play.execute(
            cid,
            Wait(
                id="wait-" + identifier,
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        command = command.model_copy(
            update={"id": identifier, "kind": kind, "expected_revision": await revision(play, cid)}
        )
        if kind == "complete":
            play.seeds = lambda: f"{540 if failure else 1:064x}"
        if kind == "complete":
            before = await play.store.read(cid)
            history, stream = await play.store.history(cid), await play.store.stream(cid)
            with pytest.raises(ConflictError):
                await service.execute(
                    cid, command.model_copy(update={"expected_revision": 0}), principal_id=principal
                )
            failing = FailingCommitPlay(play.store, play.engine, rng=secrets)
            failing.seeds = play.seeds
            with pytest.raises(RuntimeError, match="candidate checkpoint"):
                await HasteService(failing).execute(cid, command, principal_id=principal)
            assert before == await play.store.read(cid)
            assert history == await play.store.history(cid) and stream == await play.store.stream(
                cid
            )
        receipt = await service.execute(cid, command, principal_id=principal)
    final = await play.store.read(cid)
    state = play._load(final)
    canonical = parse_event(
        next(e for e in state.resources.events if e.id == event_id("complete", "haste"))
    ).result
    assert canonical.checks[0].base_target == 28
    assert canonical.checks[0].dice == ((6, 6, 6) if failure else (1, 5, 3))
    assert isinstance(receipt, SpellResult)
    assert receipt.checks == (canonical.checks if principal == "gm" else ())
    assert receipt.outcome == canonical.outcome == ("critical-failure" if failure else "active")
    assert receipt.energy_spent == canonical.energy_spent
    assert await service.execute(cid, command, principal_id=principal) == receipt
    assert await play.store.read(cid) == final
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
