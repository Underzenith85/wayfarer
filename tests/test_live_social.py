"""Live shared-time social triggers, catatonia, and durable director decisions."""

from pathlib import Path

import pytest
from test_social_dispatch import prepare

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.fright import FrightDecision, FrightService
from wayfarer.orchestration.npcs import checkpoint
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.fright import FrightEffect
from wayfarer.simulation.actions import Wait
from wayfarer.simulation.fright import apply_effect, effects
from wayfarer.simulation.npcs import (
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.simulation.resources import Advance


def test_explicit_v2_contract_is_pinned() -> None:
    from scripts.social_contracts import PATH, contract

    assert PATH.read_text() == contract()


async def test_fright_stun_defense_and_unconscious_no_defense(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.orchestration.gurps_melee import defense_value

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    ordinary, _ = defense_value(play, state, defender, "dodge")
    assert ordinary is not None and ordinary.value == 9
    resources = apply_effect(
        state.resources,
        FrightEffect(
            table_total=7,
            condition="stunned",
            duration_seconds=1,
            recovery_attribute="will",
            recovery_interval_seconds=1,
        ),
        actor_id=defender.actor_id,
        trigger_id="stun",
        command_id="stun",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    stunned = state.model_copy(update={"resources": resources})
    value, _ = defense_value(play, stunned, defender, "dodge")
    assert value is not None and value.value == 5  # Fixture Dodge 9 - B420 stun 4.
    resources = apply_effect(
        resources,
        FrightEffect(
            table_total=17,
            condition="unconscious",
            duration_seconds=60,
            recovery_attribute="ht",
            recovery_interval_seconds=60,
        ),
        actor_id=defender.actor_id,
        trigger_id="faint",
        command_id="faint",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    fainted = state.model_copy(update={"resources": resources})
    with pytest.raises(ValidationError, match="Fright condition"):
        defense_value(play, fainted, defender, "dodge")
    assert defense_value(play, fainted, defender, "none") == (None, None)


async def test_panic_response_records_choice_without_forcing_player_behavior(
    tmp_path: Path,
) -> None:
    from wayfarer.orchestration.social import ResolvedInteraction, SocialService
    from wayfarer.simulation.actions import PlayState
    from wayfarer.simulation.social import SocialCommand, SocialContext

    cid, play = await prepare(tmp_path)
    before = play._load(await play.store.read(cid))

    def resolve(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext("gurps-basic-set-4e-2004", 1, will=10, ht=10))

    play.rng = RecordedDice([6, 6, 6, 5, 5, 6, 3, 3, 3])  # B361 row 33, severity 9
    await SocialService(play, resolve).execute(
        cid,
        SocialCommand(
            id="panic",
            actor_id="a",
            subject_id="a",
            kind="fright",
            trigger_id="panic",
            expected_revision=0,
        ),
        authenticated_gm_id="gm",
    )
    play.rng = RecordedDice([6, 6, 6, 2, 2, 2])
    choice = FrightDecision(
        id="response",
        actor_id="a",
        expected_revision=1,
        fright_id="panic",
        kind="panic-response",
        response="The player resolved the first reaction.",
    )
    await FrightService(play).execute(cid, choice, authenticated_gm_id="gm")
    saved = await play.store.read(cid)
    item = effects(play._load(saved).resources)[0]
    assert item.active and item.effect.panic_severity == 6 and len(item.panic_responses) == 1
    play.rng = RecordedDice([])
    await FrightService(play).execute(cid, choice, authenticated_gm_id="gm")
    assert saved == await play.store.read(cid)
    play.rng = RecordedDice([1, 1, 1])
    await FrightService(play).execute(
        cid,
        choice.model_copy(
            update={
                "id": "second-response",
                "expected_revision": 2,
                "response": "The player resolved the second reaction.",
            }
        ),
        authenticated_gm_id="gm",
    )
    after = play._load(await play.store.read(cid))
    assert not effects(after.resources)[0].active
    assert before.actors == after.actors and before.world == after.world


async def test_npc_fright_and_failed_recovery_run_in_live_wait_transactions(tmp_path: Path) -> None:
    rules = NPCSocialRules(
        id="fear",
        version=2,
        plans=(
            NPCSocialPlan(
                id="guard-alarm",
                actor_id="npc",
                goal="Sound an alarming warning",
                first_due=1,
                interval=1,
                action_budget=1,
                actions=(
                    NPCSocialAction(
                        id="alarm",
                        kind="alarm",
                        social=NPCSocialTrigger(kind="fright", subject_id="a"),
                    ),
                ),
            ),
        ),
    )
    cid, play = await prepare(tmp_path, rules)
    play.rng = RecordedDice([4, 5, 5, 1, 1, 1])  # failure by 4 + table 3 = B360 row 7
    first = Wait(id="first", actor_id="a", expected_revision=0, ticks=1)
    await play.execute(cid, first, authenticated_actor_id="a")
    initial = play._load(await play.store.read(cid))
    assert effects(initial.resources)[0].due == 2
    assert initial.npcs.decisions[0].status == "committed"
    assert checkpoint(play, initial) == initial  # finite occurrence, not a fresh roll
    play.rng = RecordedDice([6, 6, 6, 1, 1, 1])
    second = Wait(id="second", actor_id="a", expected_revision=1, ticks=3)
    result = await play.execute(cid, second, authenticated_actor_id="a")
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.revision == state.resources.revision == 2
    assert state.resources.game_time == 4
    item = effects(state.resources)[0]
    assert not item.active and len(item.recovery_checks) == 2
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "social.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    assert await restarted.execute(cid, second, authenticated_actor_id="a") == result
    assert saved == await restarted.store.replay(cid)
    projection = await CampaignAccess(restarted).read(cid, principal_id="alice")
    assert "recovery_checks" not in str(projection) and "guard-alarm" not in str(projection)


async def test_catatonia_daily_neglect_and_total_duration_aftermath(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    initial = play._load(await play.store.read(cid)).resources
    state = apply_effect(
        initial,
        FrightEffect(
            table_total=30,
            condition="catatonia",
            duration_seconds=172800,
            recovery_attribute="ht",
            repeat_duration_dice=1,
            repeat_duration_unit=86400,
            neglect_progression=True,
            aftermath_penalty=-2,
            aftermath_seconds=172800,
        ),
        actor_id="a",
        trigger_id="fear",
        command_id="catatonia",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    # B361: day-one loss 1 HP, day-two loss 2 HP, then successful recovery.
    command = Advance(id="days", actor_id="a", expected_revision=0, to=172800)
    updated = play.engine.resources.apply(state, command, system=True, rng=RecordedDice([1] * 3))
    assert next(p.current for p in updated.pools if p.id == "hp:a") == 7
    item = effects(updated)[0]
    assert not item.active and item.aftermath_until == 345600
    assert (
        play.engine.resources.apply(updated, command, system=True, rng=RecordedDice([])) == updated
    )


async def test_coma_failed_roll_reschedules_before_requested_frontier(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    state = apply_effect(
        state,
        FrightEffect(
            table_total=29,
            condition="unconscious",
            duration_seconds=3600,
            recovery_attribute="ht",
            repeat_duration_dice=1,
            repeat_duration_unit=3600,
        ),
        actor_id="a",
        trigger_id="fear",
        command_id="coma",
        ht=10,
        will=10,
        modified_will=10,
        rng=RecordedDice([]),
    )
    updated = play.engine.resources.apply(
        state,
        Advance(id="time", actor_id="a", expected_revision=0, to=7200),
        system=True,
        rng=RecordedDice([6, 6, 6, 2]),
    )
    assert effects(updated)[0].due == 10800  # first hour + another two hours
    assert updated.game_time == 7200


async def test_care_decision_is_authorized_and_retry_safe(tmp_path: Path) -> None:
    from wayfarer.models import Event

    cid, play = await prepare(tmp_path)

    # Install the consequence through an ordinary trusted campaign transaction.
    from wayfarer.models import Campaign

    def apply(campaign: Campaign) -> Event:
        before = play._load(campaign)
        resources = apply_effect(
            before.resources,
            FrightEffect(
                table_total=30,
                condition="catatonia",
                duration_seconds=86400,
                recovery_attribute="ht",
                repeat_duration_dice=1,
                repeat_duration_unit=86400,
                neglect_progression=True,
            ),
            actor_id="a",
            trigger_id="fear",
            command_id="care-case",
            ht=10,
            will=10,
            modified_will=10,
            rng=RecordedDice([]),
        ).model_copy(update={"revision": 1})
        state = before.model_copy(update={"revision": 1, "resources": resources})
        campaign["revision"], campaign["play_json"] = 1, state.model_dump_json()
        return Event(input="seed", action="npc", outcome="fear", roll=None)

    await play.store.commit_turn(cid, "seed", 0, "seed", apply, actor_id="gm")
    command = FrightDecision(
        id="care", actor_id="a", expected_revision=1, kind="care", fright_id="care-case", care=True
    )
    with pytest.raises(ValidationError, match="director authority"):
        await FrightService(play).execute(cid, command, authenticated_gm_id="alice")
    await FrightService(play).execute(cid, command, authenticated_gm_id="gm")
    saved = await play.store.read(cid)
    await FrightService(play).execute(cid, command, authenticated_gm_id="gm")
    assert await play.store.read(cid) == saved
    with pytest.raises(ConflictError):
        await FrightService(play).execute(
            cid, command.model_copy(update={"care": False}), authenticated_gm_id="gm"
        )
    state = play._load(saved)
    advanced = play.engine.resources.apply(
        state.resources,
        Advance(id="day", actor_id="a", expected_revision=2, to=86400),
        system=True,
        rng=RecordedDice([1] * 3),
    )
    assert next(p.current for p in advanced.pools if p.id == "hp:a") == 10
