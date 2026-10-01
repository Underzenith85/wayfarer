"""B363-366/B394: an unrolled attack may be forgone, never refunded or retargeted."""

import asyncio
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_blindness_combat_consumers import Context, blind, prepare, sense
from test_combat_sensory_authority import change
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_unarmed import state_of
from test_unarmed_symptom_blindness import prepare as unarmed_prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack, history
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    ResumeInterruptedTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


async def adopt(
    path: Path, backend: str, cid: str, original: PlayService
) -> tuple[str, PlayService]:
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    return cid, play


def abandon_command(state: PlayState, *, identifier: str = "abandon") -> AbandonPendingAttack:
    encounter = state.encounters[0]
    pending = encounter.pending_defense
    unarmed = encounter.pending_unarmed
    if pending is not None:
        actor_id, pending_id = pending.attacker_id, pending.id
    else:
        assert unarmed is not None
        actor_id, pending_id = unarmed.actor_id, unarmed.id
    return AbandonPendingAttack(
        id=identifier,
        actor_id=actor_id,
        encounter_id=encounter.id,
        pending_id=pending_id,
        expected_revision=state.revision,
    )


def injury_turns(state: PlayState) -> tuple[tuple[str, int], ...]:
    return tuple((p.id, p.injury.turn) for p in state.resources.pools if p.injury is not None)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
async def test_mid_pending_blindness_can_forgo_named_attack_and_continue(
    tmp_path: Path, backend: str, context: Context
) -> None:
    if context == "square":
        cid, original = await setup(
            tmp_path / "source",
            PROFILE,
            human=True,
            durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
        )
        cid, play = await adopt(tmp_path, backend, cid, original)
        target: dict[str, object] = {"target_item_id": "sword-b"}
    else:
        cid, play = await prepare(tmp_path, backend, context)
        target = {"hit_location": "neck"}
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing", **target
    )
    await change(play, cid, lambda s: blind(s, "a"))
    if context == "square":
        await sense(play, cid, "a")
    before = await state_of(cid, play)
    with pytest.raises(ValidationError, match="random hit location|nonvisual target location"):
        await defend(cid, play, "b")
    assert await state_of(cid, play) == before
    command = abandon_command(before)
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="a")
    assert result.code == "combat.attack_abandoned" and result.current_actor_id == "b"
    after = await state_of(cid, play)
    assert after.revision == before.revision + 1
    assert after.encounters[0].pending_defense is None
    assert after.encounters[0].defense_history == before.encounters[0].defense_history
    assert after.encounters[0].wounds == before.encounters[0].wounds
    assert after.resources.items == before.resources.items
    assert [p.current for p in after.resources.pools] == [p.current for p in before.resources.pools]
    assert injury_turns(after) == injury_turns(before)
    assert after.resources.game_time == before.resources.game_time
    assert history(after)[0].pending_defense == before.encounters[0].pending_defense
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    # The other actor can take a real turn, settle one second, then play continues.
    await turn(cid, play, "b", "do_nothing")
    later = await state_of(cid, play)
    assert later.encounters[0].current_actor_id == "a"
    assert later.encounters[0].round == before.encounters[0].round + 1
    assert later.resources.game_time == before.resources.game_time + 1
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert (
        await AbandonPendingAttackService(restarted).execute(cid, command, principal_id="a")
        == result
    )
    assert await state_of(cid, restarted) == later
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_attacker_authority_payload_identity_and_atomic_race(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await state_of(cid, play)
    command = abandon_command(before)
    service = AbandonPendingAttackService(play)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, command, principal_id="b")
    with pytest.raises(ValidationError, match="Only the pending attacker"):
        await service.execute(cid, command.model_copy(update={"actor_id": "b"}), principal_id="b")
    with pytest.raises(ConflictError, match="Pending attack changed"):
        await service.execute(
            cid, command.model_copy(update={"pending_id": "unrelated"}), principal_id="a"
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(update={"expected_revision": before.revision - 1}),
            principal_id="a",
        )
    assert await state_of(cid, play) == before
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    results = await asyncio.gather(
        service.execute(cid, command, principal_id="a"),
        AbandonPendingAttackService(other).execute(cid, command, principal_id="a"),
    )
    assert results[0] == results[1]
    committed = await state_of(cid, play)
    assert committed.revision == before.revision + 1 and len(history(committed)) == 1
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"pending_id": "changed"}), principal_id="a"
        )
    with pytest.raises(ConflictError):
        await service.execute(cid, command.model_copy(update={"id": "new-stale"}), principal_id="a")
    with pytest.raises(ConflictError, match="exactly one"):
        await service.execute(
            cid,
            command.model_copy(update={"id": "again", "expected_revision": committed.revision}),
            principal_id="a",
        )
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, command, principal_id="b")
    assert await state_of(cid, play) == committed
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_double_keeps_first_strike_damage_and_all_out_restrictions(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="double",
    )
    play.rng = RecordedDice((3, 3, 3, 1))
    first = await defend(cid, play, "b")
    assert first.injury and first.injury.injury > 0
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    assert before.encounters[0].pending_defense is not None
    play.rng = RecordedDice(())
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert after.encounters[0].wounds == before.encounters[0].wounds
    assert after.encounters[0].defense_history == before.encounters[0].defense_history
    assert [p.current for p in after.resources.pools] == [p.current for p in before.resources.pools]
    actor = next(p for p in after.encounters[0].participants if p.actor_id == "a")
    assert actor.last_maneuver == "all_out_attack" and actor.maneuver_state.defense_forbidden
    assert actor.maneuver_state.attacks_remaining == 0
    assert injury_turns(after) == injury_turns(before)
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    with pytest.raises(ValidationError):
        await defend(cid, play, "a", defense="dodge")
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "a")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("action_name", ["kick", "grapple"])
async def test_unarmed_pending_attack_and_unused_second_attack_are_forgone(
    tmp_path: Path, backend: str, action_name: Literal["kick", "grapple"]
) -> None:
    cid, play = await unarmed_prepare(tmp_path, backend)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="unarmed-double",
            actor_id="a",
            target_id="b",
            encounter_id="fight",
            expected_revision=state.revision,
            action=action_name,
            maneuver="all_out_attack",
            attack_option="double",
            hands=("right-hand",) if action_name == "grapple" else (),
            enter_close_combat=action_name == "grapple",
            second_attack=UnarmedReaction(action="kick") if action_name == "grapple" else None,
        ),
        principal_id="a",
    )
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert after.encounters[0].pending_unarmed is None
    assert after.encounters[0].unarmed_history == before.encounters[0].unarmed_history
    assert after.encounters[0].close_pairs == before.encounters[0].close_pairs
    assert after.encounters[0].grips == before.encounters[0].grips
    assert after.encounters[0].current_actor_id == "b"
    actor = next(p for p in after.encounters[0].participants if p.actor_id == "a")
    assert actor.maneuver_state.second_unarmed_attack is None
    assert actor.maneuver_state.attacks_remaining == 0
    assert actor.maneuver_state.defense_forbidden
    assert history(after)[0].pending_unarmed == before.encounters[0].pending_unarmed
    assert injury_turns(after) == injury_turns(before)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await turn(cid, play, "b", "do_nothing")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wait_reaction_abandonment_restores_interrupted_turn_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "all_out_attack",
            "attack_option": "determined",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    assert (
        await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    ).code == "combat.wait_triggered"
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        attack_option="determined",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
    )
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    command = abandon_command(before)
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="a")
    after = await state_of(cid, play)
    paused = after.encounters[0]
    assert paused.wait_interrupt and paused.wait_interrupt.ready
    assert not paused.wait_interrupt.reacting and paused.current_actor_id == "b"
    assert paused.round == before.encounters[0].round
    assert after.resources.game_time == before.resources.game_time
    assert injury_turns(after) == injury_turns(before)
    assert not any(
        e.id.startswith("injury-end:") and e not in before.resources.events
        for e in after.resources.events
    )
    resume = ResumeInterruptedTurn(
        id="resume", actor_id="b", encounter_id="fight", expected_revision=after.revision
    )
    await CombatService(play).execute(cid, resume, principal_id="b")
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "a")
    finished = await state_of(cid, play)
    assert finished.encounters[0].current_actor_id == "a"
    assert finished.encounters[0].wait_interrupt is None
    assert finished.resources.game_time == before.resources.game_time + 1
    assert await AbandonPendingAttackService(play).execute(cid, command, principal_id="a") == result
    assert await state_of(cid, play) == finished


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_interposition_attack_roll_cannot_be_abandoned(
    tmp_path: Path, backend: str
) -> None:
    cid, original = await setup(tmp_path / "source", PROFILE, human=True, third_actor=True)
    cid, play = await adopt(tmp_path, backend, cid, original)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await state_of(cid, play)
    play.rng = RecordedDice((3, 3, 3, 4, 4, 4))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="protect",
            actor_id="c",
            encounter_id="fight",
            expected_revision=before.revision,
            defense="dodge",
            sacrificial_for="b",
        ),
        principal_id="c",
    )
    assert result.code == "combat.sacrificial_failed"
    await change(play, cid, lambda s: blind(s, "a"))
    committed = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Only the pending attacker"):
        await AbandonPendingAttackService(play).execute(
            cid, abandon_command(committed).model_copy(update={"actor_id": "b"}), principal_id="b"
        )
    with pytest.raises(ConflictError, match="committed attack roll"):
        await AbandonPendingAttackService(play).execute(
            cid, abandon_command(committed), principal_id="a"
        )
    assert await state_of(cid, play) == committed
    play.rng = RecordedDice((2, 2, 2))
    resolved = await defend(cid, play, "b", defense="dodge")
    assert resolved.injury and resolved.injury.attack.dice == (3, 3, 3)
    assert resolved.injury.injury == 0
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_command_reexecution_preserves_abandonment(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    await change(play, cid, lambda s: blind(s, "a"))
    before = await play.store.read(cid)
    command = abandon_command(play._load(before))
    play.rng = secrets
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="a")
    record = (await play.store.history(cid))[-1]
    assert record.reexecutable and record.entropy_seed
    replay = build_play(tmp_path / "replay", play.engine, rng=secrets)
    await seed_campaign(replay.store, before)
    await execute_recorded(replay, record)
    original_campaign = await play.store.read(cid)
    replay_campaign = await replay.store.read(cid)
    # Optional ResourceState fields can serialize in a different key order after
    # fixture seeding; the canonical state and every other envelope field match.
    assert replay._load(replay_campaign) == play._load(original_campaign)
    assert {key: value for key, value in replay_campaign.items() if key != "play_json"} == {
        key: value for key, value in original_campaign.items() if key != "play_json"
    }
    assert (await replay.store.history(cid))[-1].event["outcome"] == record.event["outcome"]
    assert (
        await AbandonPendingAttackService(replay).execute(cid, command, principal_id="a") == result
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_held_fireball_abandonment_preserves_paid_energy_and_held_effect(
    tmp_path: Path, backend: str
) -> None:
    from test_spell_bindings import command as spell_command
    from test_spell_bindings import idle, start_fight
    from test_spell_bindings import setup as spell_setup

    from wayfarer.engine.simulation.magic.spells import latest
    from wayfarer.orchestration.spells import SpellService

    cid, play = await spell_setup(tmp_path, combat=True, backend=backend, execution_version=2)
    await start_fight(cid, play)
    before_cast = await state_of(cid, play)
    start = spell_command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice((3, 3, 3))
    cast = await SpellService(play).execute(cid, start, principal_id="a")
    assert cast.energy_spent > 0
    await idle(cid, play, "b")
    before_release = await state_of(cid, play)
    play.rng = RecordedDice(())
    await SpellService(play).execute(
        cid,
        start.model_copy(
            update={
                "id": "release",
                "kind": "release",
                "expected_revision": before_release.revision,
            }
        ),
        principal_id="a",
    )
    await change(play, cid, lambda s: blind(s, "a"))
    pending = await state_of(cid, play)
    effect = latest(pending.resources)["cast"]
    assert effect.phase == "active"
    command = abandon_command(pending)
    await AbandonPendingAttackService(play).execute(cid, command, principal_id="a")
    after = await state_of(cid, play)
    assert latest(after.resources)["cast"] == effect
    fp_before = next(p.current for p in before_cast.resources.pools if p.id == "fp:a")
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == (
        fp_before - cast.energy_spent
    )
    assert after.resources.items == pending.resources.items
    assert injury_turns(after) == injury_turns(pending)
    assert after.encounters[0].current_actor_id == "b"
    await idle(cid, play, "b")
    await change(play, cid, lambda s: blind(s, "a", active=False))
    state = await state_of(cid, play)
    await SpellService(play).execute(
        cid,
        start.model_copy(
            update={"id": "release-later", "kind": "release", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    finished = await state_of(cid, play)
    assert latest(finished.resources)["cast"].phase == "ended"
    assert next(p.current for p in finished.resources.pools if p.id == "fp:a") == (
        fp_before - cast.energy_spent
    )
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_spray_remainder_retains_fired_ammunition_and_prior_rolls(
    tmp_path: Path, backend: str
) -> None:
    from test_spraying_fire import spraying_fixture

    from wayfarer.engine.simulation.combat.suppression import SprayTarget

    cid, original = await spraying_fixture(tmp_path / "source")
    cid, play = await adopt(tmp_path, backend, cid, original)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="spray",
        shots=5,
        spray_targets=(SprayTarget(target_id="c", shots=4),),
    )
    play.rng = RecordedDice((3, 3, 4, 2, 2))
    first = await defend(cid, play, "b")
    assert first.injury and first.injury.shots_fired == 5
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    assert before.encounters[0].pending_defense
    assert before.encounters[0].pending_defense.defender_id == "c"
    assert before.resources.ammunition_loads[0].rounds == 5
    play.rng = RecordedDice(())
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert after.resources.ammunition_loads == before.resources.ammunition_loads
    assert after.resources.items == before.resources.items
    assert after.encounters[0].wounds == before.encounters[0].wounds
    assert after.encounters[0].defense_history == before.encounters[0].defense_history
    assert injury_turns(after) == injury_turns(before)
    assert after.encounters[0].current_actor_id == "b"
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "c", "do_nothing")
    assert (await state_of(cid, play)).resources.game_time == before.resources.game_time + 1
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_suppression_cannot_be_cancelled_and_still_resolves_after_onset(
    tmp_path: Path, backend: str
) -> None:
    from test_suppression_fire import suppression_fixture

    from wayfarer.engine.simulation.combat.suppression import SuppressionZone
    from wayfarer.engine.simulation.hex_geometry import Hex

    cid, original = await suppression_fixture(tmp_path / "source")
    cid, play = await adopt(tmp_path, backend, cid, original)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        mode_id="suppress",
        attack_option="suppression",
        suppression_zones=(SuppressionZone(center=Hex(q=4, r=0), shots=10),),
    )
    paid = await state_of(cid, play)
    assert not paid.resources.ammunition_loads
    await change(play, cid, lambda s: blind(s, "a"))
    await turn(cid, play, "b", "move", hex_path=(Hex(q=2, r=-1), Hex(q=2, r=0)), hex_facing=3)
    before = await state_of(cid, play)
    pending = before.encounters[0].pending_defense
    assert pending and pending.suppression_zone_id and pending.interrupted_actor_id == "b"
    assert before.encounters[0].current_actor_id == "b"
    with pytest.raises(ValidationError, match="Only the pending attacker"):
        await AbandonPendingAttackService(play).execute(
            cid, abandon_command(before).model_copy(update={"actor_id": "b"}), principal_id="b"
        )
    with pytest.raises(ConflictError, match="Paid suppression"):
        await AbandonPendingAttackService(play).execute(
            cid, abandon_command(before), principal_id="a"
        )
    assert await state_of(cid, play) == before
    play.rng = RecordedDice((3, 3, 2, 3, 3, 3, 1))
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.attack.effective_target == 8
    assert result.injury.shots_fired == 10 and result.injury.hits == 1
    after = await state_of(cid, play)
    assert after.encounters[0].current_actor_id == "c"
    assert after.encounters[0].suppression_zones[0].remaining_hits == 9
    assert after.resources.ammunition_loads == paid.resources.ammunition_loads
    assert after.resources.items == before.resources.items
    assert not history(after)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wait_abandonment_preserves_mandatory_high_speed_path_then_attack_can_be_forgone(
    tmp_path: Path, backend: str
) -> None:
    from test_issue_771_high_speed_wait import course

    from wayfarer.engine.rules.types.tactical import HighSpeedState
    from wayfarer.engine.simulation.hex_geometry import Hex

    cid, original = await course(
        tmp_path / "source",
        high_speed=HighSpeedState(velocity=6, direction=4),
        third_position=Hex(q=1, r=-7),
    )
    cid, play = await adopt(tmp_path, backend, cid, original)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "zone": ((1, -1),),
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    await turn(
        cid,
        play,
        "b",
        "move_and_attack",
        item_id="sword-b",
        target_id="c",
        mode_id="swing",
        hex_path=tuple(Hex(q=1, r=-n) for n in range(1, 7)),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    mover = next(p for p in before.encounters[0].participants if p.actor_id == "b")
    assert mover.high_speed and mover.high_speed.remaining_yards == 5
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    ready = await state_of(cid, play)
    assert next(p for p in ready.encounters[0].participants if p.actor_id == "b") == mover
    assert ready.encounters[0].wait_interrupt and ready.encounters[0].wait_interrupt.ready
    resume = ResumeInterruptedTurn(
        id="resume-runner", actor_id="b", encounter_id="fight", expected_revision=ready.revision
    )
    with pytest.raises(ValidationError, match="braking"):
        await CombatService(play).execute(
            cid, resume.model_copy(update={"cancel": True}), principal_id="b"
        )
    await CombatService(play).execute(cid, resume, principal_id="b")
    await change(play, cid, lambda s: blind(s, "b"))
    resumed = await state_of(cid, play)
    runner = next(p for p in resumed.encounters[0].participants if p.actor_id == "b")
    assert runner.position == Hex(q=1, r=-6)
    assert runner.high_speed and runner.high_speed.remaining_yards is None
    assert resumed.encounters[0].pending_defense and resumed.encounters[0].wait_interrupt is None
    await AbandonPendingAttackService(play).execute(
        cid, abandon_command(resumed, identifier="abandon-runner-attack"), principal_id="b"
    )
    after = await state_of(cid, play)
    runner_after = next(p for p in after.encounters[0].participants if p.actor_id == "b")
    assert runner_after.position == runner.position and runner_after.high_speed == runner.high_speed
    assert runner_after.last_maneuver == "move_and_attack"
    assert runner_after.maneuver_state.parry_forbidden
    assert after.encounters[0].current_actor_id == "c"
    assert injury_turns(after) == injury_turns(resumed)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_shield_rush_keeps_spent_movement_without_inventing_collision_damage(
    tmp_path: Path, backend: str
) -> None:
    from test_issue_714_shield_rush import rush, rush_setup

    cid, original = await rush_setup(tmp_path / "source")
    cid, play = await adopt(tmp_path, backend, cid, original)
    await CombatService(play).execute(cid, rush(), principal_id="a")
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    assert before.encounters[0].pending_defense and before.encounters[0].pending_defense.shield_rush
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert tuple(p.position for p in after.encounters[0].participants) == tuple(
        p.position for p in before.encounters[0].participants
    )
    assert after.encounters[0].close_pairs == before.encounters[0].close_pairs
    assert after.resources.items == before.resources.items
    assert [p.current for p in after.resources.pools] == [p.current for p in before.resources.pools]
    assert after.encounters[0].current_actor_id == "b"
    assert history(after)[0].pending_defense == before.encounters[0].pending_defense
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_committed_feint_rolls_survive_forgoing_the_unrolled_attack(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    play.rng = RecordedDice((3, 3, 3, 4, 4, 4))
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="feint",
    )
    before = await state_of(cid, play)
    actor = next(p for p in before.encounters[0].participants if p.actor_id == "a")
    assert len(actor.maneuver_state.feint_rolls) == 2
    play.rng = RecordedDice(())
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert history(after)[0].spent_maneuver.feint_rolls == actor.maneuver_state.feint_rolls
    finished = next(p for p in after.encounters[0].participants if p.actor_id == "a")
    assert finished.maneuver_state.feint_rolls == ()
    assert finished.maneuver_state.defense_forbidden
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unarmed_wait_abandonment_skips_second_injury_end_and_resumes_original_attack(
    tmp_path: Path, backend: str
) -> None:
    from test_unarmed import action
    from test_unarmed import defend as unarmed_defend
    from test_unarmed_wait import armed, resume

    cid, original = await armed(tmp_path / "source")
    cid, play = await adopt(tmp_path, backend, cid, original)
    await action(cid, play, "b", "punch", target="a", hands=("right-hand",))
    await change(play, cid, lambda s: blind(s, "b"))
    before = await state_of(cid, play)
    command = abandon_command(before)
    await AbandonPendingAttackService(play).execute(cid, command, principal_id="b")
    after = await state_of(cid, play)
    assert after.encounters[0].wait_interrupt and after.encounters[0].wait_interrupt.ready
    assert after.encounters[0].current_actor_id == "a"
    assert injury_turns(after) == injury_turns(before)
    assert after.encounters[0].close_pairs == before.encounters[0].close_pairs
    await resume(cid, play)
    play.rng = RecordedDice((5, 5, 5))
    await unarmed_defend(cid, play)
    finished = await state_of(cid, play)
    assert finished.encounters[0].wait_interrupt is None
    assert finished.encounters[0].current_actor_id == "b"
    assert [(trace.actor_id, trace.action) for trace in finished.encounters[0].unarmed_history] == [
        ("a", "punch")
    ]
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_abandoning_last_actor_settles_shared_second_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    await change(play, cid, lambda s: blind(s, "b"))
    before = await state_of(cid, play)
    assert before.encounters[0].round == 1 and before.resources.game_time == 0
    command = abandon_command(before)
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="b")
    after = await state_of(cid, play)
    assert after.encounters[0].round == 2 and after.resources.game_time == 1
    assert after.encounters[0].current_actor_id == "a"
    assert injury_turns(after) == injury_turns(before)
    assert await AbandonPendingAttackService(play).execute(cid, command, principal_id="b") == result
    assert await state_of(cid, play) == after
    await turn(cid, play, "a", "do_nothing")
    assert (await state_of(cid, play)).resources.game_time == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_competing_abandonment_ids_share_one_compare_and_swap_boundary(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await state_of(cid, play)
    command = abandon_command(before)
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    results = await asyncio.gather(
        AbandonPendingAttackService(play).execute(cid, command, principal_id="a"),
        AbandonPendingAttackService(other).execute(
            cid, command.model_copy(update={"id": "competing"}), principal_id="a"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert sum(isinstance(result, CombatResult) for result in results) == 1
    after = await state_of(cid, play)
    assert after.revision == before.revision + 1 and len(history(after)) == 1
    assert after.encounters[0].current_actor_id == "b" and after.resources.game_time == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unarmed_second_attack_abandonment_keeps_the_grip_already_won(
    tmp_path: Path, backend: str
) -> None:
    from test_unarmed import defend as unarmed_defend

    cid, play = await unarmed_prepare(tmp_path, backend)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="grapple-and-kick",
            actor_id="a",
            target_id="b",
            encounter_id="fight",
            expected_revision=state.revision,
            action="grapple",
            hands=("right-hand",),
            maneuver="all_out_attack",
            attack_option="double",
            enter_close_combat=True,
            second_attack=UnarmedReaction(action="kick"),
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await unarmed_defend(cid, play)
    await change(play, cid, lambda s: blind(s, "a"))
    before = await state_of(cid, play)
    assert len(before.encounters[0].grips) == 1
    assert (
        before.encounters[0].pending_unarmed
        and before.encounters[0].pending_unarmed.action == "kick"
    )
    play.rng = RecordedDice(())
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert after.encounters[0].grips == before.encounters[0].grips
    assert after.encounters[0].unarmed_history == before.encounters[0].unarmed_history
    assert next(p for p in after.encounters[0].participants if p.actor_id == "b").grappled
    assert after.encounters[0].current_actor_id == "b"
    await turn(cid, play, "b", "do_nothing")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_abandonment_forgoes_optional_unspent_post_attack_step(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint

    cid, play = await prepare(tmp_path, backend)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        destination=GridPoint(x=0, y=1),
        step_timing="after",
    )
    before = await state_of(cid, play)
    pending = before.encounters[0].pending_defense
    assert pending and pending.post_attack_destination == GridPoint(x=0, y=1)
    await AbandonPendingAttackService(play).execute(cid, abandon_command(before), principal_id="a")
    after = await state_of(cid, play)
    assert next(p for p in after.encounters[0].participants if p.actor_id == "a").position == (
        GridPoint(x=0, y=0)
    )
    assert history(after)[0].pending_defense == pending
    assert after.encounters[0].current_actor_id == "b"
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_genesis_cannot_inject_a_recorded_abandonment_receipt(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import seed_play
    from test_actions import campaign

    from wayfarer.engine.simulation.combat.abandon import PREFIX
    from wayfarer.engine.simulation.resources import ResourceState
    from wayfarer.errors import NotFoundError

    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    state = await state_of(cid, play)
    await AbandonPendingAttackService(play).execute(cid, abandon_command(state), principal_id="a")
    before = await play.store.read(cid)
    saved = play._load(before)
    event = next(e for e in saved.resources.events if e.id.startswith(PREFIX))
    initial = campaign(play.engine)
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        await seed_play(play, initial, saved.world, ResourceState(events=(event,)), ())
    with pytest.raises(NotFoundError):
        await play.store.read(initial["id"])
    assert await play.store.read(cid) == before == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_blind_suppression_is_explicitly_unsupported_before_payment(
    tmp_path: Path, backend: str
) -> None:
    from test_suppression_fire import suppression_fixture

    from wayfarer.engine.simulation.combat.suppression import SuppressionZone
    from wayfarer.engine.simulation.hex_geometry import Hex

    cid, original = await suppression_fixture(tmp_path / "source")
    cid, play = await adopt(tmp_path, backend, cid, original)
    await change(play, cid, lambda state: blind(state, "a"))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Blind suppression fire"):
        await turn(
            cid,
            play,
            "a",
            "all_out_attack",
            item_id="sword-a",
            mode_id="suppress",
            attack_option="suppression",
            suppression_zones=(SuppressionZone(center=Hex(q=4, r=0), shots=10),),
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
