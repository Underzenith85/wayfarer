"""Selected B349 extreme-score rules reach actual pin rounds and durable replay."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_gurps_melee import setup as melee_setup
from test_social_dispatch import command as social_command
from test_social_dispatch import prepare as social_prepare
from test_unarmed import action, defend, state_of, wait

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_checks import Contestant, regular_contest, replay_regular_contest
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.social.social import SocialContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, generations
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.social_replay import (
    CapturedSocialContext,
    CapturedSocialSource,
    captured_source,
)
from wayfarer.persistence.replay import verify_commands

FEATURE = "regular-contest-extreme-scores"


async def prepare(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    source_cid, original = await melee_setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        unarmed_fixture=True,
        start_encounter=False,
    )
    source = original._load(await original.store.read(source_cid))
    play = build_play(path / "actual", original.engine, backend=backend)
    initial = campaign(play.engine)
    actors = []
    for actor in source.actors:
        purchases = tuple(
            p.model_copy(update={"amount": 14}) if p.definition_id == "attribute:st" else p
            for p in actor.proposal.draft.purchases
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        actors.append(ActorSetup(actor_id=actor.actor_id, proposal=proposal, body=actor.body))
    resources = source.resources.model_copy(
        update={
            "revision": 0,
            "items": tuple(
                i.model_copy(update={"equipped": False, "ready": False})
                for i in source.resources.items
            ),
        }
    )
    state = play.initial_state(
        initial, source.world, resources, tuple(actors), members=source.members
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=1, y=2)),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_pin_uses_source_extremes_and_retains_historical_generation(
    tmp_path: Path,
    backend: str,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", active if enabled else active - {FEATURE})
    cid, play, _ = await prepare(tmp_path, backend)
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    grip = (await state_of(cid, play)).encounters[0].grips[0]
    await wait(cid, play, "b")
    play.rng = RecordedDice((2, 2, 2, 4, 4, 4))
    await action(cid, play, "a", "takedown", grip=grip.id)
    await wait(cid, play, "b")
    state = await state_of(cid, play)
    command = TakeUnarmedTurn(
        id="pin",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        action="pin",
        target_id="b",
        grip_id=grip.id,
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((3, 3, 3, 4, 4, 3))
    result = await CombatService(play).execute(cid, command, principal_id="a")
    state = await state_of(cid, play)
    assert state.encounters[0].grips[0].pinned == enabled
    checks = state.encounters[0].unarmed_history[-1].checks
    assert [c.effective_target for c in checks] == ([10, 10] if enabled else [14, 14])
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    monkeypatch.setattr(generations, "ACTIVE", active if not enabled else active - {FEATURE})
    assert await CombatService(restarted).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pin_source_generation_reexecutes_from_original_seed(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await prepare(tmp_path, backend)
    play.rng = secrets
    play.seeds = lambda: f"{2:064x}"
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    await defend(cid, play)
    state = await state_of(cid, play)
    if not state.encounters[0].grips:
        pytest.fail("Chosen deterministic seed must produce a real grapple")
    grip = state.encounters[0].grips[0]
    await wait(cid, play, "b")
    await action(cid, play, "a", "takedown", grip=grip.id)
    state = await state_of(cid, play)
    if state.encounters[0].participants[1].posture != "prone":
        pytest.fail("Chosen deterministic seed must produce a real takedown")
    await wait(cid, play, "b")
    await action(cid, play, "a", "pin", grip=grip.id)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)


def test_regular_replay_refuses_mixed_or_tampered_per_contestant_adjustments() -> None:
    trace = regular_contest(
        "gurps-basic-set-4e-2004",
        Contestant("a", 660),
        Contestant("b", 500),
        rng=RecordedDice((4, 4, 4, 4, 4, 3)),
    )
    a, b = trace.rounds[0]
    for changed in (
        replace(b, modifiers=(replace(b.modifiers[-1], value=-489),)),
        replace(b, modifiers=(replace(b.modifiers[-1], reason="regular-contest-levelling"),)),
    ):
        with pytest.raises(ValidationError, match="does not match"):
            replay_regular_contest(replace(trace, rounds=((a, changed),)))


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("fresh", [True, False])
async def test_captured_interrogation_preserves_historical_regular_and_source_quick_contests(
    tmp_path: Path,
    backend: str,
    fresh: bool,
) -> None:
    source_id, original = await social_prepare(tmp_path / "source")
    initial = await original.store.read(source_id)
    play = build_play(
        tmp_path / "actual",
        original.engine,
        backend=backend,
        rng=secrets,
        seeds=lambda: f"{2:064x}",
    )
    await seed_campaign(play.store, initial)
    value = social_command().model_copy(update={"kind": "skill"})
    context = SocialContext(
        "gurps-basic-set-4e-2004",
        0,
        will=16,
        procedure_id="skill:interrogation",
        skill_level=16,
        conditions=frozenset({"subject-restrained", "shared-language"}),
    )
    service = SocialService(play, lambda _p, _s, _c: ResolvedInteraction(context))
    if fresh:
        expected = await service.execute(source_id, value, principal_id="gm")
    else:
        # Historical captured-source generation has no extra extrema field.
        source = CapturedSocialSource(
            context=CapturedSocialContext.model_validate(vars(context)),
            disclosure=ResolvedInteraction(context).disclosure,
        )
        assert "regular_contest_extremes" not in source.model_dump(mode="json")["context"]
        expected = await submit(
            play,
            source_id,
            service.plan(play, play._load(initial), value, principal_id="gm", source=source),
            principal_id="gm",
        )
    assert expected.outcome == ("interrogation-resolved" if fresh else "interrogation-answered")
    assert not context.regular_contest_extremes
    saved_input = await play.store.command_input(source_id, value.id)
    assert saved_input is not None
    saved_source = captured_source(saved_input)
    assert saved_source is not None
    assert saved_source.context.regular_contest_extremes == fresh
    final = await play.store.read(source_id)
    state = play._load(final)
    event = next(e for e in state.resources.events if e.id.startswith("social:"))
    private = json.loads(event.kind)["private"]
    if fresh:
        assert saved_source.context.interrogation_quick_contest
        assert private["resolution"] == "quick-contest" and private["rounds"] is None
        pair = (private["contest"]["first"], private["contest"]["second"])
    else:
        assert not saved_source.context.interrogation_quick_contest
        assert private["resolution"] == "regular-contest" and private["contest"] is None
        pair = private["rounds"]["rounds"][0]
    assert [c["effective_target"] for c in pair] == ([16, 16] if fresh else [14, 14])
    records = await played(play.store, source_id)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(source_id),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )

    def forbidden(*_args: object) -> ResolvedInteraction:
        raise AssertionError("Retry must use saved source")

    assert (
        await SocialService(restarted, forbidden).execute(source_id, value, principal_id="gm")
        == expected
    )
