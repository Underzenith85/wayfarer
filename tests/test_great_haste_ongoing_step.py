"""B237 no-ritual ongoing Steps use B368 canonical movement before the final roll."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
import test_great_haste_execution as execution_fixture
from test_great_haste_named_step import prepare_named
from test_power_maintenance_lifecycle import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.great_haste_named import NamedCastGreatHaste
from wayfarer.engine.simulation.magic.great_haste_state import (
    CHANNEL,
    CastGreatHaste,
    GreatHasteChannel,
)
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    CastingStep,
    NamedOngoingStepCastGreatHaste,
    NamedStepCastGreatHaste,
    OngoingStepCastGreatHaste,
    StepCastGreatHaste,
    leases,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def admitted(
    path: Path, backend: str, *, waiter: bool = False
) -> tuple[str, PlayService, Campaign, int]:
    from support.runtime import seed_play
    from test_actions import campaign

    from wayfarer.engine.rules.types.injury import InjuryStatus
    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.engine.simulation.magic.great_haste_state import DeclareGreatHasteChannel
    from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
    from wayfarer.engine.world import Fact
    from wayfarer.orchestration.combat import CombatService, StartEncounter

    old, play = await prepare_named(path, backend, blocked=False, amount=36, basic_move=11)
    template = play._load(await play.store.read(old))
    actors = tuple(
        ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body) for a in template.actors
    )
    if waiter:
        actors = tuple(
            a.model_copy(
                update={
                    "proposal": a.proposal.model_copy(
                        update={
                            "draft": a.proposal.draft.model_copy(
                                update={
                                    "purchases": tuple(
                                        p.model_copy(update={"amount": p.amount + 1})
                                        if p.definition_id == "attribute:dx"
                                        else p
                                        for p in a.proposal.draft.purchases
                                    )
                                }
                            )
                        }
                    )
                }
            )
            if a.actor_id == "b"
            else a
            for a in actors
        )
    world = template.world
    if waiter:
        world = replace(
            world, facts=world.facts + (Fact("caster-known", "a", "present", "dock"),)
        ).learn("b", "caster-known")
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        world,
        ResourceState(
            owners=tuple(Owner(actor_id=a, capacity=1000000) for a in ("a", "b")),
            pools=tuple(
                Pool(
                    id="hp:" + a,
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
                )
                for a in ("a", "b")
            ),
        ),
        actors,
        members=template.members,
    )
    cid = initial["id"]
    genesis = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: "00" * 32
    await GreatHasteService(play).execute(
        cid,
        DeclareGreatHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=0,
            channel=GreatHasteChannel(
                id="great-haste", actor_id="a", target_id="b", location_id="dock"
            ),
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0, facing="west"),
                Placement(actor_id="b", position=Hex(q=2, r=0), hex_facing=3, facing="east"),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, genesis, count


def ongoing(
    revision: int, *, named: bool, length: int = 2
) -> OngoingStepCastGreatHaste | NamedOngoingStepCastGreatHaste:
    common = dict(
        id="ongoing-larger",
        actor_id="a",
        expected_revision=revision,
        channel_id="great-haste",
        cast_id="ongoing",
        step=CastingStep(hex_path=(Hex(q=0, r=1), Hex(q=1, r=1))[:length]),
    )
    return (
        NamedOngoingStepCastGreatHaste.model_validate({**common, "known_fact_id": "named-subject"})
        if named
        else OngoingStepCastGreatHaste.model_validate(common)
    )


async def start(cid: str, play: PlayService, *, named: bool) -> None:
    state = play._load(await play.store.read(cid))
    common = dict(
        id="ongoing-start",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="ongoing",
    )
    command = (
        NamedCastGreatHaste.model_validate({**common, "known_fact_id": "named-subject"})
        if named
        else CastGreatHaste.model_validate(common)
    )
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "casting" and result.energy_spent == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_ongoing_larger_step_actual_final_pose_payment_seed_retry(
    tmp_path: Path, backend: str, named: bool
) -> None:
    cid, play, before, count = await admitted(tmp_path, backend)
    play.rng, play.seeds = secrets, lambda: "00" * 32
    await start(cid, play, named=named)
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["ongoing"].concentration_seconds == 1
    assert state.encounters[0].current_actor_id == "a"
    selected = ongoing(state.revision, named=named)
    legacy_model = NamedStepCastGreatHaste if named else StepCastGreatHaste
    legacy = legacy_model.model_validate(
        {**selected.model_dump(exclude={"kind"}), "id": "recorded-boundary"}
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="at most one yard"):
        await GreatHasteService(play).execute(cid, legacy, principal_id="alice")
    assert play.rng.exhausted()
    play.rng = secrets
    receipt = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
    final = await play.store.read(cid)
    state = play._load(final)
    effect = latest(state.resources)["ongoing"]
    assert receipt.outcome == "active" and receipt.energy_spent == 3
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=1
    )
    assert effect.concentration_seconds == 2 and effect.expires_at == state.resources.game_time + 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    assert leases(state.resources)[selected.id].completed
    from wayfarer.orchestration.views import campaign_view

    for member in state.members:
        projection = json.dumps(campaign_view(state, member, play.engine.rules.combat))
        assert "great-haste-step-lease:" not in projection and "named_origin_json" not in projection
    rows = (await play.store.history(cid))[count:]
    assert json.loads(rows[-1].command_input or "{}")["generation"] == (9 if named else 8)
    saved_input = json.loads(rows[-1].command_input or "{}")
    raw = saved_input["great_haste_original_input"]
    assert await play.store.duplicate(cid, selected.id, raw) == final
    with pytest.raises(ConflictError):
        await play.store.duplicate(cid, selected.id, raw + " ")
    assert await play.store.read(cid) == final

    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.orchestration.combat import CombatService, EndEncounter, TakeCombatTurn

    combat = CombatService(play)
    for i in range(3):
        current = play._load(await play.store.read(cid))
        assert current.encounters[0].current_actor_id == "b"
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="subject-action-" + str(i),
                actor_id="b",
                expected_revision=current.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    current = play._load(await play.store.read(cid))
    assert current.resources.game_time == state.resources.game_time + 1
    await combat.execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="fight",
            reason="bounded cast finished",
        ),
        principal_id="gm",
    )
    current = play._load(await play.store.read(cid))
    expire = Wait(
        id="expire",
        actor_id="a",
        expected_revision=current.revision,
        ticks=effect.expires_at - current.resources.game_time,
    )
    expired = await play.execute(cid, expire, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    assert len([e for e in state.resources.events if e.id.startswith("great-haste-ended:")]) == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 5
    assert await play.execute(cid, expire, principal_id="a") == expired
    rows = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in rows}
    replayed, checks = await verify_commands(
        before,
        rows,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "ongoing-seed"),
    )
    assert len(checks) == 9 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state
    restart = PlayService(play.store, play.engine)
    restart.rng = RecordedDice([])
    assert await GreatHasteService(restart).execute(cid, selected, principal_id="alice") == receipt
    with pytest.raises(ConflictError):
        await GreatHasteService(restart).execute(
            cid,
            selected.model_copy(update={"step": CastingStep(hex_facing=0)}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("amount,move", [(32, 11), (36, 10)])
async def test_ongoing_larger_step_source_or_move_refusal_atomic(
    tmp_path: Path, backend: str, named: bool, amount: int, move: int
) -> None:
    cid, play = await prepare_named(
        tmp_path, backend, blocked=False, amount=amount, basic_move=move
    )
    await start(cid, play, named=named)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    dice = RecordedDice([])
    play.rng = dice
    state = play._load(before)
    with pytest.raises(ValidationError):
        await GreatHasteService(play).execute(
            cid, ongoing(state.revision, named=named), principal_id="alice"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("iq_increase,allowed", [(4, False), (5, True)])
async def test_low_mana_ongoing_boundary_uses_only_approved_ritual_base(
    tmp_path: Path,
    backend: str,
    named: bool,
    iq_increase: int,
    allowed: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = CharacterCompiler
    monkeypatch.setattr(
        execution_fixture,
        "CharacterCompiler",
        lambda catalog, rules, policy, **kwargs: compiler(
            catalog, rules, replace(policy, skill_ceiling=30), **kwargs
        ),
    )
    cid, play = await prepare_named(tmp_path, backend, blocked=False, amount=36, basic_move=11)

    def low_mana_training(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        draft = actor.proposal.draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": p.amount + iq_increase})
                    if p.definition_id == "attribute:iq"
                    else p
                    for p in actor.proposal.draft.purchases
                )
            }
        )
        proposal = actor.proposal.model_copy(update={"draft": draft})
        assert play.engine.reviewer.review(proposal).compilation.build is not None, (
            play.engine.reviewer.review(proposal).compilation
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="a",
            revision=state.revision + 1,
            approver_id="gm",
            reason="B237 low-mana ritual base",
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": approval})
                    if a.actor_id == "a"
                    else a
                    for a in state.actors
                ),
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "events": tuple(
                            e.model_copy(
                                update={
                                    "kind": GreatHasteChannel.model_validate_json(e.kind)
                                    .model_copy(update={"mana": "low"})
                                    .model_dump_json()
                                }
                            )
                            if e.id.startswith(CHANNEL)
                            else e
                            for e in state.resources.events
                        )
                    }
                ),
            }
        )

    await change(cid, play, "approved-low-mana", low_mana_training)
    await start(cid, play, named=named)
    before, history = await play.store.read(cid), await play.store.history(cid)
    selected = ongoing(play._load(before).revision, named=named)
    play.rng = RecordedDice([3, 3, 3] if allowed else [])
    if allowed:
        receipt = await GreatHasteService(play).execute(cid, selected, principal_id="alice")
        assert receipt.outcome == "active" and receipt.energy_spent == 3
        assert (
            latest(play._load(await play.store.read(cid)).resources)[
                "ongoing"
            ].concentration_seconds
            == 2
        )
    else:
        with pytest.raises(ValidationError, match="ritual base skill 20"):
            await GreatHasteService(play).execute(cid, selected, principal_id="alice")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("will_success", [False, True])
async def test_ongoing_larger_step_actual_wait_will_resume_once(
    tmp_path: Path, backend: str, will_success: bool
) -> None:
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        CombatService,
        ResumeInterruptedTurn,
        TakeUnarmedTurn,
    )

    cid, play, before, count, command = await paused(tmp_path, backend)
    combat = CombatService(play)
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["ongoing"].concentration_seconds == 1
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="reaction",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            target_id="a",
            action="kick",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([1, 2, 3, 1, 2, 3] + ([1, 1, 1] if will_success else [6, 6, 6]))
    await combat.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    restart = PlayService(play.store, play.engine)
    restart.rng = RecordedDice([3, 3, 3] if will_success else [])
    resume = ResumeInterruptedTurn(
        id="resume", actor_id="a", expected_revision=state.revision, encounter_id="fight"
    )
    resumed = await CombatService(restart).execute(cid, resume, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    effect = latest(state.resources)["ongoing"]
    assert effect.phase == ("active" if will_success else "ended")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        7 if will_success else 10
    )
    if will_success:
        assert next(
            p.position for p in state.encounters[0].participants if p.actor_id == "a"
        ) == Hex(q=1, r=1)
        assert effect.concentration_seconds == 2
    assert restart.rng.exhausted()
    assert await CombatService(restart).execute(cid, resume, principal_id="a") == resumed
    assert await play.store.read(cid) == final == await play.store.replay(cid)


async def paused(
    path: Path, backend: str
) -> tuple[
    str, PlayService, Campaign, int, OngoingStepCastGreatHaste | NamedOngoingStepCastGreatHaste
]:
    from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
    from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    cid, play, before, count = await admitted(path, backend, waiter=True)
    combat = CombatService(play)
    for i in range(2):
        state = play._load(await play.store.read(cid))
        await combat.execute(
            cid,
            TakeCombatTurn(
                id="wait-" + str(i),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing" if i == 0 else "wait",
                wait_trigger=WaitTrigger(
                    actor_id="a",
                    action="move",
                    reaction="attack",
                    reaction_target_id="a",
                    unarmed=UnarmedReaction(action="kick"),
                )
                if i
                else None,
            ),
            principal_id="b",
        )
    await start(cid, play, named=True)
    state = play._load(await play.store.read(cid))
    command = ongoing(state.revision, named=True).model_copy(
        update={"step": CastingStep(hex_path=(Hex(q=1, r=0), Hex(q=1, r=1)))}
    )
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "paused"
    return cid, play, before, count, command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_ongoing_wait_original_genesis_seeded_resume_or_cancel(
    tmp_path: Path, backend: str, cancel: bool
) -> None:
    from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn

    cid, play, before, count, command = await paused(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="decline",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    restart = PlayService(play.store, play.engine)
    restart.rng, restart.seeds = secrets, lambda: "00" * 32
    resume = ResumeInterruptedTurn(
        id="resume",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        cancel=cancel,
    )
    result = await CombatService(restart).execute(cid, resume, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    assert latest(state.resources)["ongoing"].phase == ("ended" if cancel else "active")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        10 if cancel else 3 + 4
    )
    rows = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in rows}
    replayed, checks = await verify_commands(
        before,
        rows,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "wait-original-seed"),
    )
    assert len(checks) == 8 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state
    restart.rng = RecordedDice([])
    assert await CombatService(restart).execute(cid, resume, principal_id="a") == result
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "failure", ["authority", "stale", "occupied", "too-far", "entropy", "rolled-failure"]
)
async def test_ongoing_current_host_refusals_and_failed_roll(
    tmp_path: Path, backend: str, failure: str
) -> None:
    from wayfarer.errors import AuthorizationError

    cid, play, _, _ = await admitted(tmp_path, backend)
    await start(cid, play, named=False)
    before, history = await play.store.read(cid), await play.store.history(cid)
    selected = ongoing(play._load(before).revision, named=False)
    principal = "bob" if failure == "authority" else "alice"
    if failure == "stale":
        selected = selected.model_copy(update={"expected_revision": selected.expected_revision - 1})
    if failure == "occupied":
        selected = selected.model_copy(
            update={"step": CastingStep(hex_path=(Hex(q=1, r=0), Hex(q=2, r=0)))}
        )
    if failure == "too-far":
        selected = selected.model_copy(
            update={"step": CastingStep(hex_path=(Hex(q=0, r=1), Hex(q=1, r=1), Hex(q=2, r=1)))}
        )
    play.rng = RecordedDice(
        [6, 6, 5] if failure == "rolled-failure" else [3, 3] if failure == "entropy" else []
    )
    if failure == "rolled-failure":
        result = await GreatHasteService(play).execute(cid, selected, principal_id=principal)
        assert result.outcome == "failed"
        state = play._load(await play.store.read(cid))
        assert next(
            p.position for p in state.encounters[0].participants if p.actor_id == "a"
        ) == Hex(q=1, r=1)
        assert latest(state.resources)["ongoing"].phase == "ended"
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    else:
        expected = (
            AuthorizationError
            if failure == "authority"
            else ConflictError
            if failure == "stale"
            else ValidationError
        )
        with pytest.raises(expected):
            await GreatHasteService(play).execute(cid, selected, principal_id=principal)
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_competing_canonical_completion_wins_without_larger_step_partial_write(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from wayfarer.contracts import CommandReceipt, TurnResult
    from wayfarer.persistence.events import CommandEntropy, CommandOrigin, CommandResolution

    cid, play, _, _ = await admitted(tmp_path, backend)
    await start(cid, play, named=False)
    before = await play.store.read(cid)
    selected = ongoing(play._load(before).revision, named=False)
    original = play.store.commit_turn
    winners: list[Campaign] = []
    play.rng = RecordedDice([3, 3, 3])

    async def race(
        campaign_id: str,
        request_id: str,
        revision_number: int,
        text: str,
        resolve: Callable[[Campaign], CommandReceipt | CommandResolution],
        *,
        actor_id: str = "system",
        entropy: CommandEntropy | None = None,
        recorded_at_us: int | None = None,
        origin: CommandOrigin | None = None,
    ) -> TurnResult:
        if request_id == selected.id:
            winner = StepCastGreatHaste(
                id="winning",
                actor_id="a",
                expected_revision=selected.expected_revision,
                operation="concentrate",
                channel_id="great-haste",
                cast_id="ongoing",
                step=CastingStep(hex_facing=0),
            )
            plan = GreatHasteService(play).plan(
                play._load(await play.store.read(cid)),
                winner,
                "alice",
                combat_casting=True,
                ritual_step=True,
            )
            await original(
                cid,
                winner.id,
                winner.expected_revision,
                plan.payload,
                plan.resolve,
                actor_id="alice",
            )
            winners.append(await play.store.read(cid))
        return await original(
            campaign_id,
            request_id,
            revision_number,
            text,
            resolve,
            actor_id=actor_id,
            entropy=entropy,
            recorded_at_us=recorded_at_us,
            origin=origin,
        )

    monkeypatch.setattr(play.store, "commit_turn", race)
    with pytest.raises(ConflictError):
        await GreatHasteService(play).execute(cid, selected, principal_id="alice")
    assert winners and await play.store.read(cid) == winners[0]
    state = play._load(winners[0])
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=0, r=0
    )
    assert latest(state.resources)["ongoing"].phase == "active"
    assert selected.id not in leases(state.resources)
    assert await play.store.duplicate(cid, selected.id, "unused") is None
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("named", [False, True])
async def test_one_opportunity_high_skill_cast_cannot_invent_ongoing_step(
    tmp_path: Path, backend: str, named: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        execution_fixture,
        "CharacterCompiler",
        lambda catalog, rules, policy, **kwargs: CharacterCompiler(
            catalog, rules, replace(policy, skill_ceiling=30), **kwargs
        ),
    )
    cid, play = await prepare_named(tmp_path, backend, blocked=False, amount=56, basic_move=11)
    state = play._load(await play.store.read(cid))
    common = dict(
        id="one-opportunity",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        channel_id="great-haste",
        cast_id="ongoing",
    )
    first = (
        NamedCastGreatHaste.model_validate({**common, "known_fact_id": "named-subject"})
        if named
        else CastGreatHaste.model_validate(common)
    )
    play.rng = RecordedDice([3, 3, 3])
    receipt = await GreatHasteService(play).execute(cid, first, principal_id="alice")
    assert receipt.outcome == "active" and receipt.energy_spent == 2
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    assert latest(state.resources)["ongoing"].concentration_seconds == 1
    with pytest.raises(ConflictError, match="current casting concentration"):
        await GreatHasteService(play).execute(
            cid, ongoing(state.revision, named=named), principal_id="alice"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()
