"""B434/B439 personal resistance is fixed before later outside damage is selected."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_runtime
from test_combat_sensory_authority import change
from test_outside_event_host import choose, exposed_host, prepare, private_result

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.environment import poison_spec
from wayfarer.engine.rules.types.hazard import HazardSpec
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.events import HazardResolved, play_facts
from wayfarer.engine.simulation.events import visible as event_visible
from wayfarer.engine.simulation.health.hazard_visibility import PREFIX as VISIBILITY_PREFIX
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.errors import ValidationError
from wayfarer.orchestration.outside_event_prerequisites import PREFIX
from wayfarer.orchestration.outside_event_records import OutsideEventOutcome, OutsideEventPending
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands


def heat() -> HazardSpec:
    return HazardSpec(
        id="heat",
        scene_id="dock",
        kind="heat",
        delay=1800,
        interval=1800,
        cycles=2,
        reference="B434",
    )


async def due(play: PlayService, cid: str, schedule_id: str) -> None:
    while True:
        state = play._load(await play.store.read(cid))
        deadline = next(row.due for row in state.resources.hazards if row.id == schedule_id)
        if state.resources.game_time == deadline:
            return
        ticks = min(play.engine.rules.maximum_wait, deadline - state.resources.game_time)
        await play.execute(
            cid,
            Wait(
                id=f"due-{state.resources.game_time}",
                actor_id="a",
                expected_revision=state.revision,
                ticks=ticks,
            ),
            principal_id="a",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("faces,loss", [((3, 3, 3), 0), ((4, 4, 4), 1)])
async def test_heat_without_random_damage_completes_without_fake_luck_opportunity(
    tmp_path: Path,
    backend: str,
    faces: tuple[int, ...],
    loss: int,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=heat())
    await due(play, cid, schedule_id)
    rng = RecordedDice(faces)
    play.rng = rng
    _, result = await prepare(play, cid, schedule_id)
    assert result.status == "completed" and result.pending_id is None
    assert result.check and result.check.total == sum(faces)
    assert rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert snapshot(state).pending is None and not snapshot(state).luck.rolls
    assert not real_play_clock(state).cooldowns
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10 - loss
    assert state.resources.hazards[0].cycle == 1 and state.resources.hazards[0].due == 3600
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_heat_stroke_preserves_critical_ht_and_selects_only_fp_damage(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=heat())
    await due(play, cid, schedule_id)
    first_rng = RecordedDice((6, 6, 6))
    play.rng = first_rng
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OutsideEventPending) and pending.preparation.resistance
    assert pending.preparation.resistance.outcome is Outcome.CRITICAL_FAILURE
    assert pending.preparation.resistance.total == 18 and pending.preparation.dice_count == 1
    assert (
        first_rng.exhausted()
        and next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    )
    second_rng = RecordedDice((6, 2, 4))
    play.rng = second_rng
    await choose(play, cid, begun.pending_id)
    state = play._load(await play.store.read(cid))
    outcome = private_result(state).outside_event_json
    assert outcome is not None
    result = OutsideEventOutcome.model_validate_json(outcome)
    assert result.basic_damage == 2 and result.consequence and result.consequence.fp_lost == 2
    assert result.consequence.check == pending.preparation.resistance
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    assert state.resources.illnesses[0].fp_debt == 2 and second_rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cancelling_and_repreparing_secret_damage_does_not_reroll_ht(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=heat())
    await due(play, cid, schedule_id)
    play.rng = RecordedDice((6, 6, 6))
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    first = snapshot(play._load(await play.store.read(cid))).pending
    play.rng = RecordedDice(())
    await choose(play, cid, begun.pending_id, choice="cancel", identifier="cancel")
    _, reopened = await prepare(play, cid, schedule_id, secret=True, identifier="reopened")
    state = play._load(await play.store.read(cid))
    second = snapshot(state).pending
    assert isinstance(first, OutsideEventPending) and isinstance(second, OutsideEventPending)
    assert second.preparation.resistance == first.preparation.resistance
    assert len([event for event in state.resources.events if event.id.startswith(PREFIX)]) == 1
    play.rng = RecordedDice((6, 2, 4))
    await choose(play, cid, reopened.pending_id)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    assert state.resources.hazards[0].cycle == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_environmental_arsenic_keeps_ht_failure_and_applies_selected_injury(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(
        tmp_path, backend, spec=poison_spec("arsenic", id="arsenic", scene_id="dock")
    )
    await due(play, cid, schedule_id)
    prepare_rng = RecordedDice((4, 4, 4, 6))
    play.rng = prepare_rng
    _, begun = await prepare(play, cid, schedule_id)
    before = play._load(await play.store.read(cid))
    pending = snapshot(before).pending
    assert isinstance(pending, OutsideEventPending) and pending.preparation.resistance
    assert pending.preparation.resistance.effective_target == 8
    assert pending.preparation.resistance.total == 12 and prepare_rng.exhausted()
    assert next(p.current for p in before.resources.pools if p.id == "hp:a") == 10
    selected_rng = RecordedDice((2, 3))
    play.rng = selected_rng
    _, result = await choose(play, cid, begun.pending_id)
    assert result.outside_event_json is not None
    damage = OutsideEventOutcome.model_validate_json(result.outside_event_json)
    assert damage.basic_damage == 2 and damage.consequence and damage.consequence.hp_lost == 2
    assert damage.consequence.check == pending.preparation.resistance
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 8
    assert after.resources.hazards[0].symptoms == 2 and after.resources.hazards[0].remaining == 7
    assert after.resources.hazards[0].due == 7200
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    for kind in ("natural", "physician", "magic"):
        assert restore_hp(after.resources, hp, 2, kind=kind)[1] == 0
    assert selected_rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)
    await due(play, cid, schedule_id)
    play.rng = RecordedDice((1, 1, 1))
    _, recovered = await prepare(play, cid, schedule_id, identifier="next-cycle")
    assert recovered.status == "completed" and recovered.pending_id is None
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert not after.resources.hazards[0].active and not after.resources.illnesses[0].active
    assert restore_hp(after.resources, hp, 2, kind="magic")[1] == 2


async def test_follow_up_cobra_venom_is_not_implicitly_a_natural_event(tmp_path: Path) -> None:
    cid, play, schedule_id = await exposed_host(
        tmp_path, spec=poison_spec("cobra-venom", id="venom", scene_id="dock")
    )
    await due(play, cid, schedule_id)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="no supported outside"):
        await prepare(play, cid, schedule_id, secret=True)
    assert before == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_resistible_seed_reexecution_retains_the_prerequisite(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(
        tmp_path, backend, spec=poison_spec("arsenic", id="arsenic", scene_id="dock")
    )
    await due(play, cid, schedule_id)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    assert begun.status == "pending"
    before_choice = play._load(await play.store.read(cid))
    runtime = build_runtime(play)
    for visible in (
        json.dumps(await runtime.read(cid, principal_id="a")),
        json.dumps(
            [event.model_dump(mode="json") for event in await runtime.events(cid, principal_id="a")]
        ),
    ):
        assert PREFIX not in visible and '"effective_target"' not in visible
    _, owner_result = await choose(play, cid, begun.pending_id)
    assert (
        owner_result.check is None
        and owner_result.outside_event_json is None
        and owner_result.luck is None
    )
    for visible in (
        json.dumps(await runtime.read(cid, principal_id="a")),
        json.dumps(
            [event.model_dump(mode="json") for event in await runtime.events(cid, principal_id="a")]
        ),
    ):
        assert PREFIX not in visible and '"effective_target"' not in visible
    state = play._load(await play.store.read(cid))
    owner = next(member for member in state.members if member.principal_id == "a")
    gm = next(member for member in state.members if member.principal_id == "gm")
    trace_events = [
        row.event
        for row in await play.store.stream(cid)
        if isinstance(row.event, HazardResolved) and row.event.result.check is not None
    ]
    assert len(trace_events) == 1
    assert all(
        event_visible(event, gm) and not event_visible(event, owner) for event in trace_events
    )
    unmarked = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": tuple(
                        event
                        for event in state.resources.events
                        if not event.id.startswith(VISIBILITY_PREFIX)
                    )
                }
            )
        }
    )
    assert any(
        isinstance(event, HazardResolved)
        and event.result.check is not None
        and event_visible(event, owner)
        for event in play_facts(before_choice, unmarked, "a")
    )
    saved = private_result(state)
    assert saved.outside_event_json is not None
    selected = OutsideEventOutcome.model_validate_json(saved.outside_event_json)
    assert selected.consequence and selected.consequence.check
    assert (
        selected.consequence.check.total == 15 and selected.consequence.check.effective_target == 8
    )
    assert (
        selected.basic_damage == 3
        and next(p.current for p in state.resources.pools if p.id == "hp:a") == 7
    )
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)

    def demote(current: PlayState) -> PlayState:
        return current.model_copy(
            update={
                "members": tuple(
                    member.model_copy(update={"role": "player", "actor_ids": ("a",)})
                    if member.principal_id == "gm"
                    else member
                    for member in current.members
                )
            }
        )

    await change(play, cid, demote)
    demoted = next(
        member
        for member in play._load(await play.store.read(cid)).members
        if member.principal_id == "gm"
    )
    assert not any(event_visible(event, demoted) for event in trace_events)
    assert await TaskService(play).pending(cid, principal_id="gm") is None
    assert '"effective_target"' not in json.dumps(
        [event.model_dump(mode="json") for event in await runtime.events(cid, principal_id="gm")]
    )
