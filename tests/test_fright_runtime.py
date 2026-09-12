"""Independent B360-361 timed consequences and durable social dispatch evidence."""

from pathlib import Path

import pytest
from test_social_dispatch import PROFILE, command, prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.fright import apply_effect, blocked, effects, recover
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.social import SocialCommand, SocialContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import guard
from wayfarer.orchestration.social import ResolvedInteraction, SocialService


def resolve(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
    return ResolvedInteraction(SocialContext(PROFILE, 10, will=10, ht=10))


async def test_fright_loss_restart_privacy_and_recovery(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    # B360: failed by 4 + table 10 = row 14, 2 seconds stun, 3 FP lost.
    play.rng = RecordedDice([4, 5, 5, 3, 3, 4, 2, 3])
    value = command().model_copy(update={"kind": "fright", "subject_id": "a"})
    result = await SocialService(play, resolve).execute(cid, value, authenticated_gm_id="gm")
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.revision == state.resources.revision == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    assert effects(state.resources)[0].due == 2
    assert blocked(state.resources, "a")
    with pytest.raises(ValidationError, match="fright condition"):
        guard(state, "a", "move")
    with pytest.raises(ConflictError, match="fright recovery deadline"):
        play.engine.resources.apply(
            state.resources,
            Advance(
                id="skip",
                actor_id="a",
                expected_revision=1,
                to=3,
            ),
            system=True,
        )
    play.rng = RecordedDice([])
    assert (
        await SocialService(play, resolve).execute(cid, value, authenticated_gm_id="gm") == result
    )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
    projection = await CampaignAccess(play).read(cid, principal_id="alice")
    assert "recovery_target" not in str(projection)
    # First check at the end of the initial stun; failure schedules one second later.
    due = state.resources.model_copy(update={"game_time": 2})
    failed, passed = recover(
        due, actor_id="a", trigger_id="meeting", command_id="r1", rng=RecordedDice([6, 6, 6])
    )
    assert not passed and effects(failed)[0].due == 3
    recovered, passed = recover(
        failed.model_copy(update={"game_time": 3}),
        actor_id="a",
        trigger_id="meeting",
        command_id="r2",
        rng=RecordedDice([1] * 3),
    )
    assert passed and not blocked(recovered, "a")


async def test_automatic_stun_coma_schedule_and_internal_injury(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    initial = play._load(await play.store.read(cid)).resources
    for effect, expected in (
        (FrightEffect(table_total=4, condition="stunned", duration_seconds=1), 1),
        (
            FrightEffect(
                table_total=29,
                condition="unconscious",
                duration_seconds=3600,
                recovery_attribute="ht",
                repeat_duration_dice=1,
                repeat_duration_unit=3600,
                aftermath_penalty=-2,
                aftermath_seconds=21600,
            ),
            3600,
        ),
    ):
        state = apply_effect(
            initial,
            effect,
            actor_id="a",
            trigger_id="fear",
            command_id="fear",
            ht=10,
            will=10,
            modified_will=7,
            rng=RecordedDice([]),
        )
        assert effects(state)[0].due == expected
        with pytest.raises(ConflictError):
            recover(
                state, actor_id="a", trigger_id="fear", command_id="early", rng=RecordedDice([])
            )
        state = state.model_copy(update={"game_time": expected})
        state, passed = recover(
            state,
            actor_id="a",
            trigger_id="fear",
            command_id="recover",
            rng=RecordedDice([1, 1, 1]),
        )
        assert passed and not blocked(state, "a")
        if expected == 3600:
            assert effects(state)[0].aftermath_until == 25200
    injured = apply_effect(
        initial,
        FrightEffect(table_total=32, hp_loss=2),
        actor_id="a",
        trigger_id="fear",
        command_id="injury",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    assert next(p.current for p in injured.pools if p.id == "hp:a") == 8


async def test_trait_requirement_does_not_change_approved_build(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    before = play._load(await play.store.read(cid))
    # Failed by four plus table nine gives B361 row 13: a new mental quirk.
    play.rng = RecordedDice([4, 5, 5, 3, 3, 3])
    value = command().model_copy(update={"kind": "fright", "subject_id": "a"})
    outcome = await SocialService(play, resolve).execute(cid, value, authenticated_gm_id="gm")
    after = play._load(await play.store.read(cid))
    assert outcome.requires_adjudication and outcome.adjudication == ("quirk:-1",)
    assert before.actors == after.actors


async def test_recovery_command_receipt_replays_without_new_dice(tmp_path: Path) -> None:
    from test_social_dispatch import world

    from wayfarer.engine.simulation.social import apply_social

    cid, play = await prepare(tmp_path)
    initial = play._load(await play.store.read(cid)).resources
    state = apply_effect(
        initial,
        FrightEffect(
            table_total=8,
            condition="stunned",
            duration_seconds=1,
            recovery_attribute="modified-will",
            recovery_interval_seconds=1,
        ),
        actor_id="a",
        trigger_id="fear",
        command_id="original",
        ht=10,
        will=10,
        modified_will=16,
        rng=RecordedDice([]),
    ).model_copy(update={"game_time": 1})
    value = SocialCommand(
        id="recovery",
        actor_id="a",
        subject_id="a",
        trigger_id="fear",
        expected_revision=0,
        kind="fright-recovery",
    )
    updated, outcome = apply_social(
        state, world(), value, SocialContext(PROFILE, 0), rng=RecordedDice([5, 5, 5]), system=True
    )
    # B360 recovery uses modified Will 16; Rule of 14 applies only to the original fright check.
    assert outcome.outcome == "recovered"
    assert apply_social(
        updated, world(), value, SocialContext(PROFILE, 0), rng=RecordedDice([]), system=True
    ) == (updated, outcome)
