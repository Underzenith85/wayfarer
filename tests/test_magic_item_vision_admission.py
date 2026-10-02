"""B345 admission and bounded abandonment of unresolved B239/B482 item casts."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import played
from test_combat_sensory_authority import change
from test_magic_item_vision import commitment, prepare
from test_staff_casting_vision import symptoms
from test_symptom_casting_checks import finish

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellResult, latest
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


def lose_source(state: PlayState, failure: str) -> PlayState:
    if failure == "item":
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            if i.id == "blade"
                            else i
                            for i in state.resources.items
                        ),
                    }
                ),
                "encounters": tuple(
                    e.model_copy(
                        update={
                            "participants": tuple(
                                p.model_copy(
                                    update={
                                        "ready_item_ids": tuple(
                                            i for i in p.ready_item_ids if i != "blade"
                                        )
                                    }
                                )
                                for p in e.participants
                            )
                        }
                    )
                    for e in state.encounters
                ),
            }
        )
    actor = "a" if failure == "caster-approval" else "b"
    return state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"approval": None}) if a.actor_id == actor else a
                for a in state.actors
            )
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("execution_version", [1, 2])
@pytest.mark.parametrize("distance", [7, 8, 11])
@pytest.mark.parametrize("distracted", [False, True])
async def test_current_item_minimum_precedes_distraction_dice_and_keeps_cancel(
    tmp_path: Path, backend: str, execution_version: Literal[1, 2], distance: int, distracted: bool
) -> None:
    cid, play, start = await prepare(
        tmp_path, backend, distance=distance, execution_version=execution_version
    )
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    assert latest(started.resources)[start.cast_id].skill == 15 - distance
    await change(
        play,
        cid,
        lambda s: symptoms(s).model_copy(
            update={
                "resources": symptoms(s).resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 9})
                            if distracted and p.id == "hp:a"
                            else p
                            for p in s.resources.pools
                        )
                    }
                ),
            }
        ),
    )
    complete, _ = await finish(play, cid, start)
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    if distance == 7:
        dice = RecordedDice((2, 2, 2, 1, 1, 1) if distracted else (1, 1, 1))
        play.rng = dice
        result = await SpellService(play).execute(cid, complete, principal_id="a")
        assert result.checks[-1].effective_target == 3 and result.outcome == "active"
        assert dice.exhausted()
        await change(play, cid, symptoms)
        final = await play.store.read(cid)
        play.rng = RecordedDice(())
        assert await SpellService(play).execute(cid, complete, principal_id="a") == result
        assert await play.store.read(cid) == final
        return
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Effective item spell skill must be at least 3"):
        await SpellService(play).execute(cid, complete, principal_id="a")
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    before_state = play._load(before)
    assert latest(before_state.resources)[start.cast_id].phase == "casting"
    assert commitment(before_state) == commitment(started) and not dazed(
        before_state.resources, "b"
    )
    cancel = complete.model_copy(update={"id": "cancel", "kind": "cancel"})
    result = await SpellService(play).execute(cid, cancel, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.outcome == "cancelled" and result.energy_spent == 0 and result.checks == ()
    assert latest(after.resources)[start.cast_id].phase == "ended"
    assert commitment(after) == commitment(started)
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_automatic_item_completion_checks_minimum_before_dice_and_can_cancel(
    tmp_path: Path, backend: str
) -> None:
    cid, play, start = await prepare(tmp_path, backend, distance=8, combat=True)
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    await change(play, cid, symptoms)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="b-waits",
            actor_id="b",
            expected_revision=(await play.store.read(cid))["revision"],
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    complete = start.model_copy(
        update={"id": "complete", "kind": "concentrate", "expected_revision": before["revision"]}
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await SpellService(play).execute(cid, complete, principal_id="a")
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    await change(play, cid, lambda s: lose_source(s, "item"))
    cancel = complete.model_copy(
        update={
            "id": "cancel",
            "kind": "cancel",
            "expected_revision": (await play.store.read(cid))["revision"],
        }
    )
    result = await SpellService(play).execute(cid, cancel, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.outcome == "cancelled" and result.checks == () and result.energy_spent == 0
    assert latest(after.resources)[start.cast_id].phase == "ended"
    assert commitment(after) == commitment(started)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("execution_version,combat", [(1, False), (2, False), (2, True)])
async def test_historical_generation_keeps_recorded_below_three_check(
    tmp_path: Path, backend: str, execution_version: Literal[1, 2], combat: bool
) -> None:
    cid, play, start = await prepare(
        tmp_path,
        backend,
        spell="light",
        distance=13,
        execution_version=execution_version,
        combat=combat,
    )
    initial = await play.store.read(cid)
    play.rng = secrets

    async def execute(command: SpellCommand) -> SpellResult:
        value = await play.store.read(cid)
        active = play.for_campaign(value)
        return await submit(
            active,
            cid,
            SpellService(active).plan(
                active,
                member_for(active._load(value), "a"),
                command,
                principal_id="a",
                item_sight=False,
            ),
            principal_id="a",
        )

    result = await execute(start)
    complete = start if combat else (await finish(play, cid, start))[0]
    if not combat:
        result = await execute(complete)
    assert result.checks[0].effective_target == 2
    final = await play.store.read(cid)
    records = [
        r for r in await played(play.store, cid) if r.expected_revision >= initial["revision"]
    ]
    stream = await play.store.stream(cid, after=initial["revision"])
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final) and all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("execution_version", [1, 2])
@pytest.mark.parametrize("failure", ["item", "caster-approval", "target-approval"])
@pytest.mark.parametrize("principal", ["a", "gm"])
async def test_pending_cancel_uses_cast_identity_after_admission_or_source_loss(
    tmp_path: Path, backend: str, execution_version: Literal[1, 2], failure: str, principal: str
) -> None:
    cid, play, start = await prepare(tmp_path, backend, execution_version=execution_version)
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    complete, _ = await finish(play, cid, start)
    await change(play, cid, lambda s: lose_source(s, failure))
    initial = await play.store.read(cid)
    cancel = complete.model_copy(
        update={"id": "cancel", "kind": "cancel", "expected_revision": initial["revision"]}
    )
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, cancel, principal_id="b")
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, cancel.model_copy(update={"expected_revision": 0}), principal_id=principal
        )
    with pytest.raises(ConflictError, match="another actor or spell"):
        await SpellService(play).execute(
            cid, cancel.model_copy(update={"actor_id": "b"}), principal_id="b"
        )
    assert await play.store.read(cid) == initial
    play.rng = secrets
    result = await SpellService(play).execute(cid, cancel, principal_id=principal)
    final = await play.store.read(cid)
    after = play._load(final)
    assert result.outcome == "cancelled" and result.energy_spent == 0 and result.checks == ()
    assert latest(after.resources)[start.cast_id].phase == "ended"
    assert commitment(after) == commitment(started)
    assert after.resources.game_time == play._load(initial).resources.game_time
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10
    records = [
        r for r in await played(play.store, cid) if r.expected_revision >= initial["revision"]
    ]
    stream = await play.store.stream(cid, after=initial["revision"])
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, cancel, principal_id=principal) == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final) and all(c.folded and c.reexecuted for c in checks)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"actor_ids": (), "role": "spectator"})
                    if m.principal_id == principal
                    else m
                    for m in s.members
                )
            }
        ),
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await SpellService(play).execute(cid, cancel, principal_id=principal)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_spell_cancellation_still_pays_and_does_not_use_pending_escape(
    tmp_path: Path, backend: str
) -> None:
    cid, play, start = await prepare(tmp_path, backend)
    await SpellService(play).execute(cid, start, principal_id="a")
    complete, _ = await finish(play, cid, start)
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert result.outcome == "active" and result.energy_spent == 3
    await change(play, cid, lambda s: lose_source(s, "item"))
    before = await play.store.read(cid)
    cancel = complete.model_copy(
        update={"id": "cancel", "kind": "cancel", "expected_revision": before["revision"]}
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="holding a usable magic item"):
        await SpellService(play).execute(cid, cancel, principal_id="a")
    assert await play.store.read(cid) == before
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "resources": s.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": True, "equipped": True})
                            if i.id == "blade"
                            else i
                            for i in s.resources.items
                        ),
                    }
                )
            }
        ),
    )
    cancel = cancel.model_copy(
        update={"expected_revision": (await play.store.read(cid))["revision"]}
    )
    result = await SpellService(play).execute(cid, cancel, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.outcome == "cancelled" and result.energy_spent == 1
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 6
    assert await play.store.read(cid) == await play.store.replay(cid)
