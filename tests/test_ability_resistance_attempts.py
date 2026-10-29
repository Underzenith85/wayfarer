"""Actual approved ability producers retain independent B348 attempt origins."""

import asyncio
import json
import secrets
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pytest
from support.quick_contest_evidence import record_quick_contest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_abilities import command, spec
from test_ability_service import setup
from test_actions import campaign
from test_combat_sensory_authority import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_checks import ResistanceTrace
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.resistance_attempts import FailedResistanceAttempt
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.engine.simulation.abilities import internal_id
from wayfarer.engine.simulation.ability_resistance_origin import current, pending
from wayfarer.engine.simulation.ability_types import AbilityEvent
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.symptoms import register
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.ability_resistance_inputs import KEY
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


class NoDraw:
    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Unexpected draw before resistance admission or on retry")


class CountingDice:
    def __init__(self, values: tuple[int, ...]) -> None:
        self.calls = 0
        self.source = RecordedDice(values)

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        return self.source.randbelow(upper)


def seed_for(total: int) -> str:
    for number in range(10000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if sum(rng.randbelow(6) + 1 for _ in range(3)) == total:
            return seed
    raise AssertionError("No source total seed")


async def fixture(
    path: Path,
    backend: str,
    kind: str,
    distance: int,
    *,
    paid_cost: bool = True,
    principal: str = "a",
    caster_iq: int = 10,
    caster_will: int = 10,
    resister_iq: int = 10,
    resister_will: int = 10,
) -> tuple[str, PlayService, Campaign]:
    modifiers = (("malediction-1",) if kind == "burning-malediction" else ()) + (
        ("costs-fatigue-1",) if paid_cost else ()
    )
    cid, donor = await setup(path / "donor", spec(kind, modifiers))
    source = donor._load(await donor.store.read(cid))
    rules = donor.engine.rules
    assert rules.abilities
    rules = rules.model_copy(
        update={
            "abilities": rules.abilities.model_copy(
                update={
                    "channels": tuple(
                        c.model_copy(update={"distance_yards": distance})
                        for c in rules.abilities.channels
                    )
                }
            )
        }
    )
    engine = ActionEngine(donor.engine.reviewer, donor.engine.resources, rules)
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: seed_for(12)
    )
    initial = campaign(engine)
    actors = []
    for actor in source.actors:
        purchases = tuple(
            p.model_copy(update={"amount": caster_iq if actor.actor_id == "a" else resister_iq})
            if p.definition_id == "attribute:iq"
            else p
            for p in actor.proposal.draft.purchases
        ) + (
            Purchase(
                definition_id="secondary:will",
                amount=caster_will if actor.actor_id == "a" else resister_will,
            ),
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        actors.append(
            ActorSetup(actor_id=actor.actor_id, proposal=proposal, body=HumanBody(anatomy="human"))
        )
    state = play.initial_state(
        initial,
        source.world,
        source.resources,
        tuple(actors),
        members=source.members
        + (
            (CampaignMember(principal_id=principal, role="player", actor_ids=("a",)),)
            if principal != "a"
            else ()
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play, initial


async def start(play: PlayService, cid: str, *, legacy: bool = False, principal: str = "a") -> None:
    value = command((await play.store.read(cid))["revision"])
    service = AbilityService(play)
    if legacy:
        state = play._load(await play.store.read(cid))
        member = next(m for m in state.members if m.principal_id == principal)
        await submit(
            play,
            cid,
            service.plan(play, state, member, value, principal_id=principal),
            principal_id=principal,
        )
    else:
        await service.execute(cid, value, principal_id=principal)
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="one-second", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,distance", [("burning-malediction", 2), ("mind-reading", 5)])
@pytest.mark.parametrize("legacy", [False, True])
async def test_failed_actual_attack_has_only_real_checks_and_original_genesis_replay(
    tmp_path: Path, backend: str, kind: str, distance: int, legacy: bool
) -> None:
    principal = "player:alice"
    cid, play, genesis = await fixture(tmp_path, backend, kind, distance, principal=principal)
    await start(play, cid, legacy=legacy, principal=principal)
    before = play._load(await play.store.read(cid))
    value = command(before.revision, "resolve")
    assert current(before.resources, value) == (not legacy)
    result, duplicate = await asyncio.gather(
        *(AbilityService(play).execute(cid, value, principal_id=principal) for _ in range(2))
    )
    assert duplicate == result and result.outcome == "resisted" and not result.revealed_fact_ids
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id == internal_id(value.id))
    native = AbilityEvent.model_validate_json(event.kind)
    assert len(native.checks) == (2 if legacy else 1)
    assert native.checks[0].effective_target == 8 and native.checks[0].total == 12
    assert native.failed and not native.critical_failure
    if legacy:
        assert native.resistance_attempt is None and native.resistance_preparation is None
        assert "resistance_attempt" not in event.kind
    else:
        assert isinstance(native.resistance_attempt, FailedResistanceAttempt)
        assert native.resistance_attempt.declared_resister.base_target == 10
        assert native.resistance_attempt.rule_of_16
        assert native.resistance_preparation is not None
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert state.resources.game_time == 1 and not pending(state.resources)
    saved = await play.store.command_input(cid, value.id)
    assert saved and saved.text
    data = replay_payload(saved.text)
    assert isinstance(data, dict) and (KEY in data) == (not legacy)
    history, stream, final = (
        await played(play.store, cid),
        await play.store.stream(cid),
        await play.store.read(cid),
    )
    play.rng = no_draw = NoDraw()
    assert await AbilityService(play).execute(cid, value, principal_id=principal) == result
    with pytest.raises(AuthorizationError):
        await AbilityService(play).execute(
            cid,
            value.model_copy(update={"id": "foreign-actor", "expected_revision": state.revision}),
            principal_id="b",
        )
    with pytest.raises(ConflictError):
        await AbilityService(play).execute(
            cid, value.model_copy(update={"channel_id": "changed"}), principal_id=principal
        )
    assert no_draw.calls == 0 and await played(play.store, cid) == history
    assert await play.store.read(cid) == final and await play.store.stream(cid) == stream
    for actor in ("a", "b"):
        visible = json.dumps(await build_runtime(play).read(cid, principal_id=actor))
        assert KEY not in visible and "resistance_preparation" not in visible
    replayed, checks = await verify_commands(
        genesis,
        history,
        stream,
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks), [
        (c.command_id, c.reason) for c in checks
    ]
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,distance", [("burning-malediction", 8), ("mind-reading", 50)])
@pytest.mark.parametrize("empty_fp", [False, True])
async def test_current_activation_two_refuses_before_health_entropy_or_cost(
    tmp_path: Path, backend: str, kind: str, distance: int, empty_fp: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, kind, distance, paid_cost=False)
    if empty_fp:
        await change(
            play,
            cid,
            lambda s: s.model_copy(
                update={
                    "resources": s.resources.model_copy(
                        update={
                            "pools": tuple(
                                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                                for p in s.resources.pools
                            )
                        }
                    )
                }
            ),
        )
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = no_draw = NoDraw()
    value = command(before["revision"])
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await AbilityService(play).execute(cid, value, principal_id="a")
    assert no_draw.calls == 0 and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) is None


def cough_after_wound(state: PlayState) -> PlayState:
    resources, injury = apply_injury(
        state.resources,
        Wound(
            id="recent-wound",
            actor_id="a",
            expected_revision=state.resources.revision,
            basic_damage=6,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice((1, 1, 1)),
        system=True,
    )
    assert injury.injury == 6
    resources = register(
        resources,
        actor_id="a",
        source_id="authored-symptom-source",
        injury_id="recent-wound",
        amount=6,
        pool_id="hp:a",
        spec=SymptomSpec(kind="coughing"),
    )
    return state.model_copy(update={"resources": resources})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_three_to_two_keeps_original_cost_and_pending_until_native_cancel(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, "mind-reading", 30)
    await start(play, cid)
    initial_pending = play._load(await play.store.read(cid))
    assert pending(initial_pending.resources)
    assert next(p.current for p in initial_pending.resources.pools if p.id == "fp:a") == 9
    # Trusted GM fixture transition uses canonical injury/Symptoms reducers;
    # it does not claim a new public damage producer or a purchased B109 recipe.
    await change(play, cid, cough_after_wound)
    before = await play.store.read(cid)
    state = play._load(before)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    value = command(state.revision, "resolve")
    play.rng = no_draw = NoDraw()
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await AbilityService(play).execute(cid, value, principal_id="a")
    assert no_draw.calls == 0 and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) is None
    assert pending(state.resources) and state.resources.game_time == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 4
    cancel = command(state.revision, "cancel")
    result = await AbilityService(play).execute(cid, cancel, principal_id="a")
    assert result.outcome == "cancelled" and no_draw.calls == 0
    final = play._load(await play.store.read(cid))
    assert not pending(final.resources) and final.resources.game_time == 1
    assert next(p.current for p in final.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


def seed_for_pair(first: int, second: int, damage: int | None = None) -> str:
    for number in range(10000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        a = sum(rng.randbelow(6) + 1 for _ in range(3))
        b = sum(rng.randbelow(6) + 1 for _ in range(3))
        die = rng.randbelow(6) + 1 if damage is not None else None
        if (a, b, die) == (first, second, damage):
            return seed
    raise AssertionError("No exact native source pair")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["burning-malediction", "mind-reading"])
@pytest.mark.parametrize("tie", [False, True])
async def test_current_actual_subject_roll_ties_resist_and_winning_attack_affects_once(
    tmp_path: Path, backend: str, kind: str, tie: bool
) -> None:
    burning = kind == "burning-malediction"
    cid, play, genesis = await fixture(
        tmp_path,
        backend,
        kind,
        2,
        caster_iq=12,
        caster_will=14,
        resister_will=15 if tie else 10,
    )
    play.seeds = lambda: seed_for_pair(9, 12, 4 if burning and not tie else None)
    await start(play, cid)
    before = play._load(await play.store.read(cid))
    value = command(before.revision, "resolve")
    result = await AbilityService(play).execute(cid, value, principal_id="a")
    state = play._load(await play.store.read(cid))
    native = AbilityEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == internal_id(value.id))
    )
    attempt = native.resistance_attempt
    assert isinstance(attempt, ResistanceTrace) and len(native.checks) == 2
    assert attempt.attacker.effective_target == 12 and attempt.attacker.total == 9
    assert attempt.resister.effective_target == (15 if tie else 10) and attempt.resister.total == 12
    assert attempt.affected == (not tie)
    assert attempt.contest.victory_margin == (0 if tie else 5)
    assert result.outcome == ("resisted" if tie else "hit" if burning else "active")
    assert result.revealed_fact_ids == (() if tie or burning else ("thought",))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        6 if burning and not tie else 10
    )
    assert native.damage_dice == ((4,) if burning and not tie else ())
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    history, stream = await played(play.store, cid), await play.store.stream(cid)
    play.rng = no_draw = NoDraw()
    assert await AbilityService(play).execute(cid, value, principal_id="a") == result
    assert no_draw.calls == 0 and await played(play.store, cid) == history
    replayed, checks = await verify_commands(
        genesis,
        history,
        stream,
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks), [
        (c.command_id, c.reason) for c in checks
    ]
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_mandatory_subject_will_one_is_still_actually_rolled(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        "burning-malediction",
        2,
        caster_will=14,
        resister_iq=5,
        resister_will=1,
    )
    await start(play, cid)
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 4))
    value = command((await play.store.read(cid))["revision"], "resolve")
    result = await AbilityService(play).execute(cid, value, principal_id="a")
    state = play._load(await play.store.read(cid))
    event = AbilityEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == internal_id(value.id))
    )
    assert isinstance(event.resistance_attempt, ResistanceTrace)
    assert event.resistance_attempt.resister.effective_target == 1
    assert event.resistance_attempt.resister.total == 3
    assert result.outcome == "hit" and event.damage_dice == (4,)
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_final_three_reaches_real_original_and_can_succeed(
    tmp_path: Path, backend: str
) -> None:
    cid, play, genesis = await fixture(tmp_path, backend, "mind-reading", 30)
    play.seeds = lambda: seed_for_pair(3, 18)
    await start(play, cid)
    before = await play.store.read(cid)
    value = command(before["revision"], "resolve")
    play.rng = no_draw = NoDraw()
    with pytest.raises(AssertionError, match="Unexpected draw"):
        await AbilityService(play).execute(cid, value, principal_id="a")
    assert no_draw.calls == 1 and await play.store.read(cid) == before
    play.rng = secrets
    result = await AbilityService(play).execute(cid, value, principal_id="a")
    state = play._load(await play.store.read(cid))
    event = AbilityEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == internal_id(value.id))
    )
    assert isinstance(event.resistance_attempt, ResistanceTrace)
    assert (
        event.resistance_attempt.attacker.effective_target
        == event.resistance_attempt.attacker.total
        == 3
    )
    assert event.resistance_attempt.resister.total == 18 and event.resistance_attempt.affected
    assert result.outcome == "active" and result.revealed_fact_ids == ("thought",)
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_old_origin_two_remains_historical_on_a_fresh_resolve(
    tmp_path: Path, backend: str
) -> None:
    cid, play, genesis = await fixture(tmp_path, backend, "mind-reading", 50)
    play.seeds = lambda: seed_for_pair(3, 18)
    await start(play, cid, legacy=True)
    state = play._load(await play.store.read(cid))
    value = command(state.revision, "resolve")
    assert not current(state.resources, value)
    # This is authentic old producer compatibility, not a legal current target2.
    result = await AbilityService(play).execute(cid, value, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    event = AbilityEvent.read_native_json(
        next(e.kind for e in state.resources.events if e.id == internal_id(value.id))
    )
    assert event.resistance_attempt is None and event.resistance_preparation is None
    assert len(event.checks) == 2 and event.checks[0].effective_target == 2
    assert event.checks[0].total == 3 and event.checks[1].total == 18
    assert result.outcome == "active"
    play.rng = no_draw = NoDraw()
    assert await AbilityService(play).execute(cid, value, principal_id="a") == result
    assert no_draw.calls == 0 and await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["burning-malediction", "mind-reading"])
@pytest.mark.parametrize("branch", ["failed", "tie", "win"])
async def test_current_literal_actual_draw_chronology(
    tmp_path: Path,
    backend: str,
    kind: str,
    branch: str,
    record_property: Callable[[str, object], None],
) -> None:
    burning = kind == "burning-malediction"
    failed = branch == "failed"
    tie = branch == "tie"
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        kind,
        (2 if burning else 5) if failed else 2,
        caster_iq=10 if failed else 12,
        caster_will=10 if failed else 14,
        resister_will=15 if tie else 10,
    )
    initial = play._load(await play.store.read(cid))
    actor = next(a for a in initial.actors if a.actor_id == "a")
    actor_build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    target = next(a for a in initial.actors if a.actor_id == "b")
    target_build, _ = play.engine.reviewer.activate(
        target.proposal, target.approval, campaign_id=cid, actor_id="b"
    )
    compiler = play.engine.reviewer.compiler
    assert compiler.statistics_profile == "gurps-basic-set-4e-2004"
    actor_values = {v.target: int(v.value) for v in actor_build.sheet.values}
    target_values = {v.target: int(v.value) for v in target_build.sheet.values}
    compiled_actor_score = actor_values["secondary:will" if burning else "attribute:iq"]
    assert compiled_actor_score == ((10 if failed else 14) if burning else (10 if failed else 12))
    activation = command(initial.revision)
    assert (
        await AbilityService(play).execute(cid, activation, principal_id="a")
    ).outcome == "concentrating"
    activated = play._load(await play.store.read(cid))
    activation_event = AbilityEvent.read_native_json(
        next(e.kind for e in activated.resources.events if e.id == internal_id(activation.id))
    )
    assert activation_event.effect is not None
    activation_duration = activation_event.effect.ready_at - activation_event.effect.started_at
    activation_fp = next(p.current for p in activated.resources.pools if p.id == "fp:a")
    assert activation_duration == 1 and activated.resources.game_time == 0 and activation_fp == 9
    wait = Wait(id="one-second", actor_id="a", expected_revision=activated.revision, ticks=1)
    await play.execute(cid, wait, principal_id="a")
    before = play._load(await play.store.read(cid))
    assert before.resources.game_time == 1
    values: tuple[int, ...] = (4, 4, 4) if failed else (3, 3, 3, 4, 4, 4)
    if burning and branch == "win":
        values += (4,)
    play.rng = dice = CountingDice(values)
    value = command((await play.store.read(cid))["revision"], "resolve")
    result = await AbilityService(play).execute(cid, value, principal_id="a")
    state = play._load(await play.store.read(cid))
    event = AbilityEvent.read_native_json(
        next(e.kind for e in state.resources.events if e.id == internal_id(value.id))
    )
    assert dice.calls == len(values) and dice.source.exhausted()
    assert len(event.checks) == (1 if failed else 2)
    assert event.checks[0].effective_target == (8 if failed else 12)
    assert event.checks[0].dice == ((4, 4, 4) if failed else (3, 3, 3))
    assert event.resistance_attempt is not None
    assert event.resistance_attempt.affected == (branch == "win")
    if not failed:
        assert isinstance(event.resistance_attempt, ResistanceTrace)
        assert event.resistance_attempt.resister.dice == (4, 4, 4)
        assert event.resistance_attempt.resister.effective_target == (15 if tie else 10)
    assert result.outcome == ("resisted" if branch != "win" else "hit" if burning else "active")
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        6 if burning and branch == "win" else 10
    )
    assert result.revealed_fact_ids == (("thought",) if not burning and branch == "win" else ())
    preparation = event.resistance_preparation
    assert preparation is not None
    resister_modifiers = [modifier.value for modifier in preparation.resister.modifiers]
    assert preparation.attacker.base_target == (8 if failed else 12)
    assert preparation.attacker.modifiers == ()
    assert (
        preparation.resister.base_target == target_values["secondary:will"] == (15 if tie else 10)
    )
    assert preparation.resister.modifiers == ()
    assert event.resistance_attempt.rule_of_16 is True
    assert state.resources.game_time == before.resources.game_time == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == activation_fp
    assert play.engine.rules.abilities is not None
    channel = next(c for c in play.engine.rules.abilities.channels if c.id == activation.channel_id)
    assert channel.distance_yards == ((2 if burning else 5) if failed else 2)
    inputs: dict[str, object] = {
        "compiled_actor_score": compiled_actor_score,
        "distance_yards": channel.distance_yards,
        "native_trace_attacker_base": event.checks[0].base_target,
        "native_trace_attacker_modifiers": [m.value for m in event.checks[0].modifiers],
        "declared_resister_base": preparation.resister.base_target,
        "resister_modifiers": resister_modifiers,
        "attacker_dice": list(event.checks[0].dice),
        "resister_dice": list(event.checks[1].dice) if len(event.checks) == 2 else None,
        "rule_of_16": event.resistance_attempt.rule_of_16,
    }
    observed: dict[str, object] = {
        "attacker_margin": event.checks[0].margin,
        "subject_attempted": isinstance(event.resistance_attempt, ResistanceTrace),
        "affected": event.resistance_attempt.affected,
        "contest_draws": dice.calls - len(event.damage_dice),
        "target_hp": next(p.current for p in state.resources.pools if p.id == "hp:b"),
        "revealed_fact_ids": list(result.revealed_fact_ids),
        "original_activation_fp_after": activation_fp,
        "damage_dice": list(event.damage_dice),
        "declared_activation_duration_seconds": activation_duration,
        "clock_after_activation": activated.resources.game_time,
        "explicit_wait_seconds": before.resources.game_time - activated.resources.game_time,
        "clock_before_and_after_resolve": state.resources.game_time,
    }
    if isinstance(event.resistance_attempt, ResistanceTrace):
        observed["resister_margin"] = event.resistance_attempt.resister.margin
        observed["victory_margin"] = event.resistance_attempt.contest.victory_margin
    record_property("b348_runtime_profile", compiler.statistics_profile)
    record_property("b348_runtime_rules", json.dumps(asdict(actor_build.rules), sort_keys=True))
    family = {"failed": "r7", "tie": "r8", "win": "r9"}[branch]
    record_quick_contest(
        f"basic-b348-{family}-ability-{kind}-literal-draws", inputs, observed, record_property
    )
