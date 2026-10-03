"""B368 initial movement precedes B236 first casting action, with actual Step budget."""

import json
import secrets
from pathlib import Path

import pytest
from test_great_haste_named_step import other_turns, prepare_named

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    CastingStep,
    InitialStepCastGreatHaste,
    NamedInitialStepCastGreatHaste,
    NamedStepCastGreatHaste,
    StepCastGreatHaste,
    leases,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.replay import verify_commands

Initial = InitialStepCastGreatHaste | NamedInitialStepCastGreatHaste


def initial(
    revision: int, *, named: bool, path: tuple[Hex, ...] = (Hex(q=0, r=1), Hex(q=1, r=1))
) -> Initial:
    common = dict(
        id="initial-larger",
        actor_id="a",
        expected_revision=revision,
        channel_id="great-haste",
        cast_id="larger",
        step=CastingStep(hex_path=path),
    )
    return (
        NamedInitialStepCastGreatHaste.model_validate({**common, "known_fact_id": "named-subject"})
        if named
        else InitialStepCastGreatHaste.model_validate(common)
    )


def continuation(
    revision: int, index: int, *, named: bool
) -> StepCastGreatHaste | NamedStepCastGreatHaste:
    common = dict(
        id="larger-second-" + str(index),
        actor_id="a",
        expected_revision=revision,
        operation="concentrate",
        channel_id="great-haste",
        cast_id="larger",
        step=CastingStep(hex_facing=0),
    )
    return (
        NamedStepCastGreatHaste.model_validate({**common, "known_fact_id": "named-subject"})
        if named
        else StepCastGreatHaste.model_validate(common)
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_larger_initial_step_paid_cast_seed_retry_and_actual_range(
    tmp_path: Path, backend: str, named: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    before = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(before)
    play.rng, play.seeds = secrets, lambda: "00" * 32
    service = GreatHasteService(play)
    selected = initial(state.revision, named=named)
    first = await service.execute(cid, selected, principal_id="alice")
    assert first.outcome == "casting" and first.energy_spent == 0
    moved = play._load(await play.store.read(cid))
    encounter = moved.encounters[0]
    assert next(p.position for p in encounter.participants if p.actor_id == "a") == Hex(q=1, r=1)
    assert encounter.current_actor_id == "a" and encounter.maneuver_budget is not None
    assert encounter.maneuver_budget.remaining == 1
    lease = leases(moved.resources)[selected.id]
    assert lease.completed and lease.command == selected
    assert latest(moved.resources)["larger"].concentration_seconds == 1
    await service.execute(cid, continuation(moved.revision, 1, named=named), principal_id="alice")
    await other_turns(cid, play)
    state = play._load(await play.store.read(cid))
    final_command = continuation(state.revision, 2, named=named)
    receipt = await service.execute(cid, final_command, principal_id="alice")
    final = await play.store.read(cid)
    state = play._load(final)
    effect = latest(state.resources)["larger"]
    assert receipt.outcome == "active" and receipt.energy_spent == 4
    assert effect.skill == 14 and effect.expires_at == state.resources.game_time + 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 6
    for member in state.members:
        projection = json.dumps(campaign_view(state, member, play.engine.rules.combat))
        assert "great-haste-step-lease:" not in projection and "named_origin_json" not in projection
    records = (await play.store.history(cid))[count:]
    assert json.loads(records[0].command_input or "{}")["generation"] == (7 if named else 6)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "initial-seed"),
    )
    assert len(checks) == 5 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state
    restart = PlayService(play.store, play.engine)
    restart.rng = RecordedDice([])
    assert await GreatHasteService(restart).execute(cid, selected, principal_id="alice") == first
    assert (
        await GreatHasteService(restart).execute(cid, final_command, principal_id="alice")
        == receipt
    )
    with pytest.raises(ConflictError):
        await GreatHasteService(restart).execute(
            cid,
            selected.model_copy(update={"step": CastingStep(hex_path=(Hex(q=1, r=0),))}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == final and restart.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_initial_step_current_allowance_authority_and_stale_are_atomic(
    tmp_path: Path, backend: str, named: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11, amount=4)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    service = GreatHasteService(play)
    selected = initial(state.revision, named=named)
    play.rng = RecordedDice([])
    with pytest.raises(AuthorizationError):
        await service.execute(
            cid, selected.model_copy(update={"actor_id": "b"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            selected.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="alice",
        )
    with pytest.raises(ValidationError):
        await service.execute(
            cid,
            initial(
                state.revision, named=named, path=(Hex(q=0, r=1), Hex(q=1, r=1), Hex(q=2, r=1))
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()
    # Initial base12 is lawful before casting begins, distinct from ongoing ritual movement.
    assert (await service.execute(cid, selected, principal_id="alice")).outcome == "casting"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(("move", "length"), [(20, 2), (21, 3)])
async def test_initial_step_uses_full_actual_move_tier(
    tmp_path: Path, backend: str, named: bool, move: int, length: int
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=move)
    state = play._load(await play.store.read(cid))
    path = (Hex(q=0, r=1), Hex(q=1, r=1), Hex(q=2, r=1))[:length]
    play.rng = RecordedDice([])
    receipt = await GreatHasteService(play).execute(
        cid, initial(state.revision, named=named, path=path), principal_id="alice"
    )
    current = play._load(await play.store.read(cid))
    assert receipt.outcome == "casting" and receipt.energy_spent == 0
    assert (
        next(p.position for p in current.encounters[0].participants if p.actor_id == "a")
        == path[-1]
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_initial_step_blocked_intermediate_cell_refuses_atomically(
    tmp_path: Path, backend: str, named: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_great_haste_named_subject as source_fixture

    original = source_fixture.board
    monkeypatch.setattr(
        source_fixture,
        "board",
        lambda blocked: original(blocked).model_copy(
            update={
                "cells": tuple(
                    cell.model_copy(update={"blocked": True})
                    if cell.position == Hex(q=0, r=1)
                    else cell
                    for cell in original(blocked).cells
                )
            }
        ),
    )
    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await GreatHasteService(play).execute(
            cid, initial(state.revision, named=named), principal_id="alice"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("success", [False, True])
async def test_initial_larger_cast_refreshes_moving_subject_and_final_entropy_rollback(
    tmp_path: Path, backend: str, named: bool, success: bool
) -> None:
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    cid, play = await prepare_named(tmp_path, backend, blocked=False, basic_move=11)
    service = GreatHasteService(play)
    state = play._load(await play.store.read(cid))
    await service.execute(cid, initial(state.revision, named=named), principal_id="alice")
    state = play._load(await play.store.read(cid))
    await service.execute(cid, continuation(state.revision, 1, named=named), principal_id="alice")
    combat = CombatService(play)
    state = play._load(await play.store.read(cid))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="subject-moves",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            hex_path=(Hex(q=1, r=0), Hex(q=0, r=0)),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="subject-second",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    selected = continuation(state.revision, 2, named=named)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="dice"):
        await service.execute(cid, selected, principal_id="alice")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([4, 4, 2] if success else [5, 5, 5])
    receipt = await service.execute(cid, selected, principal_id="alice")
    current = play._load(await play.store.read(cid))
    effect = latest(current.resources)["larger"]
    assert receipt.outcome == ("active" if success else "failed")
    assert receipt.energy_spent == (4 if success else 1)
    assert effect.skill == 13 and effect.target_id == "b"
    assert next(pool.current for pool in current.resources.pools if pool.id == "fp:a") == (
        6 if success else 9
    )
    assert play.rng.exhausted()
