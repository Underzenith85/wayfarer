"""Cancelled and mixed routes share the same canonical hazard obligations."""

import secrets
from pathlib import Path
from typing import Never

import pytest
from support.runtime import build_play, build_runtime
from test_combat_sensory_authority import change
from test_outside_event_host import choose, exposed_host, prepare
from test_outside_event_prerequisites import due, heat
from test_secret_task_boundaries import revoke_owner

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environment import poison_spec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.events import HazardResolved, visible
from wayfarer.engine.simulation.health.hazards import HazardCommand, HazardResult
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.hazards import HazardService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tasks import real_play_clock
from wayfarer.persistence.replay import verify_commands


def ordinary_service(play: PlayService) -> HazardService:
    def no_new_source(*_: object) -> Never:
        raise AssertionError("Resolving a recorded exposure must not fetch another source")

    return HazardService(play, no_new_source)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("seeded", [False, True])
async def test_cancelled_secret_ht_is_preserved_by_ordinary_resolution_after_restart(
    tmp_path: Path,
    backend: str,
    seeded: bool,
) -> None:
    cid, play, sid = await exposed_host(tmp_path, backend, spec=heat())
    await due(play, cid, sid)
    play.rng = RecordedDice((6, 6, 6))
    _, begun = await prepare(play, cid, sid, secret=True)
    play.rng = RecordedDice(())
    await choose(play, cid, begun.pending_id, choice="cancel", identifier="cancel")
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="disclosure"):
        await prepare(play, cid, sid, identifier="cannot-publish-ht")
    assert before == await play.store.read(cid)
    dice = RecordedDice((3,))
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=dice, instants=play.instants)
    initial = await restarted.store.read(cid)
    count = len(await restarted.store.history(cid))
    if seeded:
        restarted.rng = secrets
        restarted.seeds = lambda: "ab" * 32
    state = restarted._load(await restarted.store.read(cid))
    command = HazardCommand(
        id="ordinary",
        actor_id="a",
        expected_revision=state.revision,
        kind="resolve",
        hazard_id="heat",
    )
    result = await ordinary_service(restarted).execute(cid, command, principal_id="a")
    assert result.check is None and result.consciousness is None and result.fp_lost == 3
    saved = await restarted.store.read(cid)
    state = restarted._load(saved)
    actual = HazardResult.model_validate_json(
        next(event.kind for event in state.resources.events if event.id == "hazard:ordinary")
    )
    assert actual.check and actual.check.total == 18 and actual.fp_lost == 3
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 7
    assert state.resources.hazards[0].cycle == 1 and state.resources.hazards[0].due == 3600
    assert not real_play_clock(state).cooldowns
    assert seeded or dice.exhausted()
    owner = next(member for member in state.members if member.principal_id == "a")
    events = [
        row.event
        for row in await restarted.store.stream(cid)
        if isinstance(row.event, HazardResolved) and row.event.result.check
    ]
    assert len(events) == 1 and not any(visible(event, owner) for event in events)
    restarted.rng = RecordedDice(())
    assert await ordinary_service(restarted).execute(cid, command, principal_id="a") == result
    assert saved == await restarted.store.read(cid) == await restarted.store.replay(cid)
    runtime = build_runtime(restarted)
    owner_stream = await runtime.events(cid, principal_id="a")
    assert next(row.outcome for row in owner_stream if row.command_id == command.id) == ""
    assert not any('"base_target"' in row.outcome for row in owner_stream)
    gm_stream = await runtime.events(cid, principal_id="gm")
    gm_outcome = next(row.outcome for row in gm_stream if row.command_id == command.id)
    assert HazardResult.model_validate_json(gm_outcome) == actual
    with pytest.raises(ValidationError, match="authenticated actor"):
        await ordinary_service(restarted).execute(cid, command, principal_id="gm")
    if seeded:
        records = (await restarted.store.history(cid))[count:]
        ids = {record.command_id for record in records}
        replayed, checks = await verify_commands(
            initial,
            records,
            [row for row in await restarted.store.stream(cid) if row.command_id in ids],
            configuration_digest=state.configuration_digest,
            execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
        )
        assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
        assert replayed == saved

    def demote_and_observe(current: PlayState) -> PlayState:
        return current.model_copy(
            update={
                "members": tuple(
                    member.model_copy(update={"role": "player", "actor_ids": ("a",)})
                    if member.principal_id == "gm"
                    else member
                    for member in current.members
                )
                + (CampaignMember(principal_id="spectator", role="spectator"),)
            }
        )

    await change(restarted, cid, demote_and_observe)
    demoted = runtime.member(await runtime.checkpoint(cid), "gm")
    assert not any(visible(event, demoted) for event in events)
    with pytest.raises(ValidationError, match="authenticated actor"):
        await ordinary_service(restarted).execute(cid, command, principal_id="gm")
    for principal in ("gm", "spectator"):
        stream = await runtime.events(cid, principal_id=principal)
        assert next(row.outcome for row in stream if row.cursor == state.revision) == ""
        assert not any('"base_target"' in row.outcome for row in stream)
        paged = await runtime.events(cid, principal_id=principal, after=state.revision - 1, limit=1)
        assert len(paged) == 1 and paged[0].outcome == ""
    await change(restarted, cid, revoke_owner)
    with pytest.raises(AuthorizationError):
        await ordinary_service(restarted).execute(cid, command, principal_id="a")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("selected_cycle", [0, 1])
async def test_all_mixed_poison_cycles_accumulate_debt_and_final_cycle_releases_it(
    tmp_path: Path,
    backend: str,
    selected_cycle: int,
) -> None:
    spec = poison_spec("arsenic", id="arsenic", scene_id="dock")
    cid, play, sid = await exposed_host(tmp_path, backend, spec=spec)
    last_ordinary = None
    last_result = None
    first_ordinary = None
    first_result = None
    first_input = None
    for cycle in range(8):
        await due(play, cid, sid)
        play.rng = RecordedDice((4, 4, 4, 1))
        if cycle == selected_cycle:
            _, begun = await prepare(play, cid, sid, identifier=f"prepared-{cycle}")
            play.rng = RecordedDice(())
            await choose(
                play, cid, begun.pending_id, choice="resolve", identifier=f"selected-{cycle}"
            )
        else:
            state = play._load(await play.store.read(cid))
            last_ordinary = HazardCommand(
                id=f"ordinary-{cycle}",
                actor_id="a",
                expected_revision=state.revision,
                kind="resolve",
                hazard_id="arsenic",
            )
            last_result = await ordinary_service(play).execute(cid, last_ordinary, principal_id="a")
            if first_ordinary is None:
                first_ordinary, first_result = last_ordinary, last_result
                first_input = await play.store.command_input(cid, last_ordinary.id)
        state = play._load(await play.store.read(cid))
        hp = next(pool for pool in state.resources.pools if pool.id == "hp:a")
        restriction = next(row for row in state.resources.illnesses if row.id == sid)
        assert hp.current == 9 - cycle and restriction.hp_debt == cycle + 1
        assert restriction.active == state.resources.hazards[0].active == (cycle < 7)
        for kind in ("natural", "physician", "magic"):
            assert restore_hp(state.resources, hp, 10, kind=kind)[1] == (0 if cycle < 7 else 8)
        if cycle == 1:
            play = build_play(
                tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
            )
    assert state.resources.hazards[0].remaining == 0
    saved = await play.store.read(cid)
    assert saved == await play.store.replay(cid)
    assert last_ordinary is not None and last_result is not None
    play.rng = RecordedDice(())
    assert await ordinary_service(play).execute(cid, last_ordinary, principal_id="a") == last_result
    assert await play.store.read(cid) == saved
    assert first_ordinary is not None and first_result is not None
    assert (
        await ordinary_service(play).execute(cid, first_ordinary, principal_id="a") == first_result
    )
    assert await play.store.command_input(cid, first_ordinary.id) == first_input
    assert await play.store.read(cid) == saved
