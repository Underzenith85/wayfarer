"""Immediate corrected social commands retain their private source and seed replay."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_runtime
from test_gadgeteer_gizmos_persistence import RevokingStore
from test_social_dispatch import PROFILE, command, prepare, resolve

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import ReactionModifier
from wayfarer.engine.rules.social.social_hooks import Standing
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.orchestration.social_replay import CapturedSocialContext, captured_source
from wayfarer.persistence.events import CommandInput
from wayfarer.persistence.replay import unavailable_reason, verify_commands


def forbidden(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
    raise AssertionError("A retry or seed replay must not call a live resolver")


@pytest.mark.parametrize("kind", ["reaction", "influence", "skill"])
async def test_immediate_reaction_captures_private_source_and_reexecutes_seed(
    tmp_path: Path,
    kind: str,
) -> None:
    cid, play = await prepare(tmp_path)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    value = command().model_copy(update={"kind": kind})

    def source(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        interaction = resolve(play, state, command)
        interaction.context.target = 20
        if kind == "skill":
            interaction.context.procedure_id = "skill:diplomacy"
            interaction.context.skill_level = 20
            interaction.context.conditions = frozenset(
                {"audience-perceptible", "audience-audible", "shared-language"}
            )
        return interaction

    result = await SocialService(play, source).execute(cid, value, principal_id="gm")
    saved = await play.store.read(cid)
    assert result.outcome == "good"
    assert ("a", "disclosure") in play._load(saved).world.knowledge
    records = await play.store.history(cid)
    record = records[-1]
    assert record.command_input is not None
    raw = validation.mapping(replay_payload(record.command_input))
    recorded_source = validation.mapping(raw["social_source"])
    assert validation.mapping(recorded_source["context"])["required_fact_ids"] == ["secret"]
    assert validation.mapping(recorded_source["disclosure"])["fact_ids"] == ["disclosure"]
    captured = captured_source(CommandInput(record.payload_hash, record.command_input))
    assert captured is not None
    assert all(modifier.kind != "trait" for modifier in captured.context.modifiers)
    replayed, checks = await verify_commands(
        initial,
        [record],
        [event for event in await play.store.stream(cid) if event.command_id == record.command_id],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-replay"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == saved == await play.store.replay(cid)
    play.rng = RecordedDice(())
    assert await SocialService(play, forbidden).execute(cid, value, principal_id="gm") == result
    assert saved == await play.store.read(cid)
    with pytest.raises(ConflictError):
        await SocialService(play, forbidden).execute(
            cid, value.model_copy(update={"trigger_id": "changed"}), principal_id="gm"
        )
    runtime = build_runtime(play)
    actor_view = json.dumps(await runtime.read(cid, principal_id="alice"))
    actor_events = json.dumps(
        [row.model_dump(mode="json") for row in await runtime.events(cid, principal_id="alice")]
    )
    assert "social_source" not in actor_view + actor_events
    assert "secret" not in actor_view + actor_events


async def test_unauthorized_or_lost_authority_retry_never_calls_resolver(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    with pytest.raises(ValidationError, match="director authority"):
        await SocialService(play, forbidden).execute(cid, command(), principal_id="alice")
    await SocialService(play, resolve).execute(cid, command(), principal_id="gm")
    play.engine.reviewer.gm_ids = frozenset()
    revoked = PlayService(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(ValidationError, match="director authority"):
        await SocialService(revoked, forbidden).execute(cid, command(), principal_id="gm")


async def test_legacy_immediate_source_remains_fold_only_and_retry_byte_exact(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    service = SocialService(play, resolve)
    value = command()
    # The pre-capture plan encodes only the original command/principal.
    expected = await submit(
        play,
        cid,
        service.plan(play, play._load(initial), value, principal_id="gm"),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    record = (await play.store.history(cid))[-1]
    assert unavailable_reason(record) == "legacy social command has no captured resolver source"
    folded, checks = await verify_commands(
        initial,
        [record],
        [event for event in await play.store.stream(cid) if event.command_id == record.command_id],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "unused"),
    )
    assert folded == saved and checks[0].folded and not checks[0].reexecuted
    play.rng = RecordedDice(())
    assert await SocialService(play, forbidden).execute(cid, value, principal_id="gm") == expected
    assert (await play.store.history(cid))[-1] == record
    assert await play.store.read(cid) == saved


def test_source_round_trip_retains_conditions_and_does_not_reuse_mutated_binding() -> None:
    context = SocialContext(
        PROFILE,
        11,
        ht=12,
        will=13,
        modifiers=(ReactionModifier("situation", -2, "fog"),),
        standing=Standing(appearance="attractive"),
        procedure_id="skill:diplomacy",
        skill_level=14,
        partner_skill=15,
        conditions=frozenset({"good-tools"}),
        required_fact_ids=("answer",),
    )
    captured = CapturedSocialContext.model_validate(vars(context))
    restored = CapturedSocialContext.model_validate_json(captured.model_dump_json())
    first = restored.resolve()
    first.bind_trait_modifiers((ReactionModifier("trait", 1, "approved"),))
    second = restored.resolve()
    assert second.modifiers == (ReactionModifier("situation", -2, "fog"),)
    assert second.conditions == frozenset({"good-tools"})
    assert (second.ht, second.will, second.skill_level, second.partner_skill) == (12, 13, 14, 15)
    assert second.required_fact_ids == ("answer",)
    assert second.standing == Standing(appearance="attractive")


def test_capture_identity_does_not_depend_on_python_set_hash_seed() -> None:
    import os
    import subprocess
    import sys

    code = (
        "from wayfarer.orchestration.social_replay import CapturedSocialContext; "
        "print(CapturedSocialContext(profile_id='gurps-basic-set-4e-2004', target=12, "
        "conditions=frozenset({'shared-language','audience-audible','audience-perceptible'}))"
        ".model_dump_json())"
    )
    captures = [
        subprocess.check_output(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        )
        for seed in ("1", "27")
    ]
    assert captures[0] == captures[1]
    assert json.loads(captures[0])["conditions"] == [
        "audience-audible",
        "audience-perceptible",
        "shared-language",
    ]


@pytest.mark.parametrize("retry", [False, True])
async def test_live_director_trust_is_checked_after_duplicate_lookup(
    tmp_path: Path,
    retry: bool,
) -> None:
    cid, original = await prepare(tmp_path)
    if retry:
        await SocialService(original, resolve).execute(cid, command(), principal_id="gm")
    before = await original.store.read(cid)
    store = RevokingStore(tmp_path / "social.sqlite", 10)
    play = PlayService(store, original.engine, rng=RecordedDice(()))

    async def revoke() -> None:
        play.engine.reviewer.gm_ids = frozenset()

    store.revoke = revoke
    with pytest.raises(ValidationError, match="director authority"):
        await SocialService(play, forbidden if retry else resolve).execute(
            cid, command(), principal_id="gm"
        )
    assert await original.store.read(cid) == before
