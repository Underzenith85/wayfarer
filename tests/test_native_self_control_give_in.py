"""B120's reviewed give-in decision through native trusted social dispatch.

This is director-authorized recording of a subject's declared decision, not a
new player transport or automatic control of a player character's behavior.
"""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, played
from test_condition_self_control import BAD_TEMPER, COWARDICE, PROFILE, fixture, source
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext, apply_social
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.advancement import AdvancementService, GrantPoints
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import InteractionResolver, ResolvedInteraction, SocialService
from wayfarer.orchestration.social_generations import social_generation
from wayfarer.orchestration.social_replay import CapturedSocialContext, CapturedSocialSource
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


async def native_fixture(path: Path, backend: str) -> tuple[str, PlayService]:
    if backend == "postgres":
        assert os.environ.get("WAYFARER_TEST_DATABASE_URL"), "PostgreSQL evidence cannot skip"
    return await fixture(path, backend, BAD_TEMPER)


def command(revision: int, *, identifier: str = "give-in") -> SocialCommand:
    return SocialCommand(
        id=identifier,
        actor_id="npc",
        subject_id="a",
        kind="self-control",
        trigger_id="declared-provocation",
        expected_revision=revision,
    )


def giving_in(*, trait_id: str = BAD_TEMPER) -> InteractionResolver:
    original = source(BAD_TEMPER, modifier=50)

    def resolve(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        interaction = original(play, state, value)
        interaction.context.self_control_give_in = True
        interaction.context.self_control_trait_id = trait_id
        return interaction

    return resolve


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("banked_points", [0, 2])
async def test_native_give_in_uses_no_dice_or_points_and_retries_replays_privately(
    tmp_path: Path, backend: str, banked_points: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await native_fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    if banked_points:
        await AdvancementService(play).grant(
            cid,
            GrantPoints(
                id="banked-two-points",
                actor_id="gm",
                target_actor_id="a",
                expected_revision=initial["revision"],
                points=banked_points,
                reason="Keep actual banked points unchanged while recording the declared decision",
            ),
            principal_id="gm",
        )
    before = play._load(await play.store.read(cid))
    value = command(before.revision)
    draws: list[int] = []
    actual = SeededRandom.randbelow

    def observed(rng: SeededRandom, bound: int, /) -> int:
        draws.append(bound)
        return actual(rng, bound)

    monkeypatch.setattr(SeededRandom, "randbelow", observed)
    outcome = await SocialService(play, giving_in()).execute(cid, value, principal_id="gm")
    assert draws == []
    assert outcome.kind == "self-control" and outcome.outcome == "triggered"
    assert not outcome.requires_adjudication and not outcome.adjudication
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.actors == before.actors and state.world == before.world
    assert state.resources.pools == before.resources.pools
    assert state.advancement == before.advancement
    assert sum(entry.points for entry in state.advancement) == banked_points
    event = next(e for e in state.resources.events if e.id.startswith("social:"))
    private = json.loads(event.kind)["private"]
    assert private == {
        "decision": "give-in",
        "resisted": False,
        "dice": None,
        "points_spent": 0,
    }
    record = (await play.store.history(cid))[-1]
    assert record.reexecutable and record.command_input is not None
    payload = replay_payload(record.command_input)
    assert isinstance(payload, dict)
    captured = payload["social_source"]["context"]
    assert captured["self_control_give_in"] is True
    assert captured["self_control_trait_id"] == BAD_TEMPER
    player = next(m for m in state.members if m.principal_id == "alice")
    projection = json.dumps(campaign_view(state, player))
    assert "self_control_give_in" not in projection and "points_spent" not in projection
    assert "decision" not in outcome.model_dump_json() and "dice" not in outcome.model_dump_json()
    saved_input = await play.store.command_input(cid, value.id)
    history, stream = await play.store.history(cid), await play.store.stream(cid)

    def unavailable(*_: object) -> ResolvedInteraction:
        raise AssertionError("An accepted retry must not consult a fresh decision")

    restarted = build_play(
        tmp_path / "restart",
        play.engine,
        store=play.store,
        seeds=lambda: (_ for _ in ()).throw(AssertionError("Retry must not capture new entropy")),
    )
    assert (
        await SocialService(restarted, unavailable).execute(cid, value, principal_id="gm")
        == outcome
    )
    assert await play.store.read(cid) == final
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) == saved_input
    with pytest.raises(ConflictError):
        await SocialService(restarted, unavailable).execute(
            cid, value.model_copy(update={"trigger_id": "different"}), principal_id="gm"
        )
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "original-seed"),
    )
    assert len(checks) == 1 + bool(banked_points)
    assert all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
    assert final == await play.store.replay(cid) and draws == []


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("principal", ["alice", "a", "outsider"])
async def test_player_or_nonmember_cannot_declare_native_give_in(
    tmp_path: Path, backend: str, principal: str
) -> None:
    cid, play = await native_fixture(tmp_path, backend)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)

    def unavailable(*_: object) -> ResolvedInteraction:
        raise AssertionError("An unauthorized caller must not invoke the trusted resolver")

    play.seeds = lambda: (_ for _ in ()).throw(AssertionError("Refusal captured entropy"))
    refusal = NotFoundError if principal == "outsider" else ValidationError
    message = "Campaign not found" if principal == "outsider" else "trusted director"
    with pytest.raises(refusal, match=message):
        await SocialService(play, unavailable).execute(
            cid, command(before["revision"]), principal_id=principal
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, "give-in") is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trait_id", [COWARDICE, "trait:unknown"])
async def test_give_in_still_requires_the_actual_approved_disadvantage(
    tmp_path: Path, backend: str, trait_id: str
) -> None:
    cid, play = await native_fixture(tmp_path, backend)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.seeds = lambda: (_ for _ in ()).throw(AssertionError("Invalid trait captured entropy"))
    with social_generation(False, self_control=False):
        with pytest.raises(ValidationError, match="approved disadvantage"):
            await SocialService(play, giving_in(trait_id=trait_id)).execute(
                cid, command(before["revision"]), principal_id="gm"
            )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, "give-in") is None


@pytest.mark.parametrize("wrong", ["kind", "lite", "prototype", "integer", "text"])
async def test_new_decision_refuses_wrong_kind_profile_or_nonboolean_before_entropy(
    tmp_path: Path, wrong: str
) -> None:
    cid, play = await native_fixture(tmp_path, "sqlite")
    before = await play.store.read(cid)
    value = command(before["revision"])
    if wrong == "kind":
        value = value.model_copy(update={"kind": "reaction"})
    original = giving_in()

    def invalid(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        interaction = original(play, state, value)
        if wrong in ("lite", "prototype"):
            interaction.context.profile_id = (
                "gurps-lite-4e-2004" if wrong == "lite" else "package:wayfarer-lite"
            )
        elif wrong in ("integer", "text"):
            vars(interaction.context)["self_control_give_in"] = 1 if wrong == "integer" else "yes"
        return interaction

    play.seeds = lambda: (_ for _ in ()).throw(AssertionError("Invalid decision captured entropy"))
    with pytest.raises(ValidationError, match="self-control|Self-control"):
        await SocialService(play, invalid).execute(cid, value, principal_id="gm")
    assert await play.store.read(cid) == before
    assert await play.store.command_input(cid, value.id) is None


def test_default_roll_capture_is_exact_and_strict_decision_is_private() -> None:
    context = SocialContext(PROFILE, 10)
    captured = CapturedSocialContext.model_validate(vars(context))
    assert "self_control_give_in" not in captured.model_dump(mode="json")
    assert not captured.resolve().self_control_give_in
    with pytest.raises(SchemaError):
        CapturedSocialContext.model_validate(vars(context) | {"self_control_give_in": 1})
    with pytest.raises(SchemaError):
        SocialCommand.model_validate(command(0).model_dump() | {"self_control_give_in": True})
    decoded = CapturedSocialSource.model_validate_json(
        json.dumps({"context": captured.model_dump(mode="json"), "disclosure": {}})
    )
    assert not decoded.context.self_control_give_in
    assert decoded.context.model_dump_json() == captured.model_dump_json()


def test_direct_engine_refuses_explicit_give_in_on_a_non_self_control_check() -> None:
    from test_social_dispatch import world

    context = SocialContext(PROFILE, 10, self_control_give_in=True)
    value = command(0).model_copy(update={"kind": "reaction"})

    class NoDice:
        def randbelow(self, _bound: int, /) -> int:
            raise AssertionError("A refused decision must not roll")

    from wayfarer.engine.simulation.resources import ResourceState

    with pytest.raises(ValidationError, match="Basic Set self-control"):
        apply_social(ResourceState(), world(), value, context, rng=NoDice(), system=True)
