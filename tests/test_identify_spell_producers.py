"""Real producer receipts and completion-time Information eligibility (B249)."""

from pathlib import Path

import pytest
from support.identify_spell import fixture, producer, revision
from support.runtime import build_runtime

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
from wayfarer.engine.simulation.magic.identify_spell_state import (
    CastIdentifySpell,
    IdentifySubject,
    ObserveIdentifySpellSubject,
    SecretIdentification,
    secret_result,
)
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLock, DeclareLockChannel
from wayfarer.engine.simulation.magic.lock_state import LockFixture, LockState
from wayfarer.engine.simulation.magic.spell_state import event_id, latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.identify_spell import IdentifySpellService
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.play import PlayService


async def _observe(play: PlayService, cid: str, subject: str) -> None:
    await IdentifySpellService(play).execute(
        cid,
        ObserveIdentifySpellSubject(
            id="identify-physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(id="identify-subject", caster_id="c", subject_id=subject),
        ),
        principal_id="gm",
    )


async def _identify(play: PlayService, cid: str) -> SecretIdentification:
    before = play._load(await play.store.read(cid))
    play.seeds = lambda: f"{1:064x}"
    await build_runtime(play).submit_json(
        cid,
        CastIdentifySpell(
            id="identify-producers",
            actor_id="c",
            expected_revision=before.revision,
            cast_id="identification",
            subject_id="identify-subject",
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    state = play._load(await play.store.read(cid))
    secret = secret_result(state.resources, "identification")
    assert secret is not None and secret.check.outcome.succeeded
    assert secret.at == state.resources.game_time == before.resources.game_time + 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 8
    assert await play.store.read(cid) == await play.store.replay(cid)
    return secret


async def _wait(play: PlayService, cid: str, seconds: int, name: str) -> None:
    await build_runtime(play).submit_json(
        cid,
        Wait(
            id=name, actor_id="a", expected_revision=await revision(play, cid), ticks=seconds
        ).model_dump(mode="json"),
        principal_id="alice",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("age,included", [(5, True), (6, False)])
async def test_recent_cast_boundary_uses_identification_completion_not_observation_or_start(
    tmp_path: Path, backend: str, age: int, included: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    state = play._load(await play.store.read(cid))
    accepted = next(
        e for e in state.resources.events if e.id == event_id("source-complete", "haste")
    )
    start_event = next(
        e for e in state.resources.events if e.id == event_id("source-start", "haste")
    )
    assert accepted.at - start_event.at == 2
    await _observe(play, cid, "b")
    await _wait(play, cid, age - 1, "wait-to-identify")
    before = play._load(await play.store.read(cid))
    assert before.resources.game_time - accepted.at == age - 1
    secret = await _identify(play, cid)
    assert secret.at - accepted.at == age
    assert bool(secret.spells) is included
    assert secret.descriptions == (("Haste",) if included else ())
    if included:
        assert len(secret.spells) == 1
        found = secret.spells[0]
        assert (found.cast_id, found.at, found.event_id, found.status) == (
            "source",
            accepted.at,
            accepted.id,
            "completed",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_maintenance_does_not_refresh_recent_cast_flash(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    await _observe(play, cid, "b")
    before = play._load(await play.store.read(cid))
    effect = latest(before.resources)["source"]
    assert effect.expires_at is not None
    await _wait(play, cid, effect.expires_at - before.resources.game_time, "wait-to-maintain")
    await HasteService(play).execute(
        cid,
        RuntimeSpellCommand(
            id="real-maintenance",
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="maintain",
            spell_id="haste",
            channel_id="source-haste",
            cast_id="source",
        ),
        principal_id="alice",
    )
    maintained = play._load(await play.store.read(cid))
    assert latest(maintained.resources)["source"].expires_at == effect.expires_at + 60
    maintenance = next(
        e for e in maintained.resources.events if e.id == event_id("real-maintenance", "haste")
    )
    secret = await _identify(play, cid)
    assert secret.at - maintenance.at == 1
    assert secret.spells == () and secret.descriptions == ()
    assert latest(play._load(await play.store.read(cid)).resources)["source"].phase == "active"


async def _second_self_haste(play: PlayService, cid: str) -> None:
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id="second-haste-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(
                id="second-haste", actor_id="a", target_id="a", location_id="dock"
            ),
        ),
        principal_id="gm",
    )

    async def execute(kind: str) -> None:
        await service.execute(
            cid,
            RuntimeSpellCommand.model_validate(
                dict(
                    id="second-" + kind,
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    kind=kind,
                    spell_id="haste",
                    channel_id="second-haste",
                    cast_id="second-source",
                    energy=1,
                )
            ),
            principal_id="alice",
        )

    await execute("start")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["second-source"]
    for second in range(effect.ready_at - effect.started_at):
        await _wait(play, cid, 1, "second-casting-" + str(second))
        await execute(
            "complete" if second == effect.ready_at - effect.started_at - 1 else "concentrate"
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "subject,expected", [("a", ("second-source", "source")), ("b", ("source",))]
)
async def test_all_actual_on_or_by_producers_are_bound_to_current_subject(
    tmp_path: Path, backend: str, subject: str, expected: tuple[str, ...]
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await producer(play, cid)
    await _second_self_haste(play, cid)
    await _observe(play, cid, subject)
    secret = await _identify(play, cid)
    assert tuple(s.cast_id for s in secret.spells) == expected
    assert secret.descriptions == ("Haste",)
    assert all(s.caster_id == "a" for s in secret.spells)
    assert tuple(s.subject_id for s in secret.spells) == (("a", "b") if subject == "a" else ("b",))
    assert all(s.status == "completed" and 0 <= secret.at - s.at <= 5 for s in secret.spells)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_long_cast_currently_casting_qualifies_after_start_flash_is_old(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        producer_iq=12,
        extra_purchases=(
            Purchase(definition_id="spell:apportation"),
            Purchase(definition_id="spell:lockmaster"),
        ),
    )
    physical = LockService(play)
    await physical.execute(
        cid,
        DeclareLock(
            id="real-lock",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            state=LockState(
                fixture=LockFixture(object_id="chest", location_id="dock", kind="lock"),
                locked=True,
                closed=True,
            ),
        ),
        principal_id="gm",
    )
    await physical.execute(
        cid,
        DeclareLockChannel(
            id="real-lock-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=LockChannel(
                id="long-lockmaster",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="lockmaster",
            ),
        ),
        principal_id="gm",
    )
    service = LockSpellService(play)

    async def cast_second(kind: str, name: str) -> None:
        await service.execute(
            cid,
            RuntimeSpellCommand.model_validate(
                dict(
                    id=name,
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    kind=kind,
                    spell_id="lockmaster",
                    channel_id="long-lockmaster",
                    cast_id="long-source",
                )
            ),
            principal_id="gm",
        )

    await cast_second("start", "long-start")
    original = latest(play._load(await play.store.read(cid)).resources)["long-source"]
    assert original.ready_at - original.started_at == 10
    for second in range(6):
        await _wait(play, cid, 1, "long-second-" + str(second))
        await cast_second("concentrate", "long-concentrate-" + str(second))
    current = latest(play._load(await play.store.read(cid)).resources)["long-source"]
    assert current.phase == "casting" and current.concentration_seconds == 7
    await _observe(play, cid, "a")
    secret = await _identify(play, cid)
    assert secret.at - original.started_at == 7 and secret.at < current.ready_at
    assert len(secret.spells) == 1 and secret.descriptions == ("Lockmaster",)
    found = secret.spells[0]
    assert (found.cast_id, found.event_id, found.at, found.status, found.caster_id) == (
        "long-source",
        event_id("long-start", "lockmaster"),
        original.started_at,
        "casting",
        "a",
    )
