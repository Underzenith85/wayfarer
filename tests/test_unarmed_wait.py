"""Independent Basic Set expectations for unarmed Wait reactions: B366, B370-371.

The selected Campaigns fourth printing is the declared source.
Expected values are written from that baseline, never read back from the code here.
"""

from pathlib import Path

import pytest
from test_unarmed import action, defend, setup, state_of, wait

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat import CombatResult
from wayfarer.engine.simulation.maneuvers import WaitTrigger
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    CombatService,
    ResumeInterruptedTurn,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

PUNCH = {"action": "punch", "hands": ("right-hand",)}


def declaration(**changes: object) -> dict[str, object]:
    trigger: dict[str, object] = {
        "actor_id": "a",
        "action": "attack",
        "reaction": "attack",
        "reaction_target_id": "a",
        "unarmed": dict(PUNCH),
    }
    trigger.update(changes)
    return trigger


async def declare(
    cid: str,
    play: PlayService,
    actor: str = "b",
    **changes: object,
) -> CombatResult:
    state = await state_of(cid, play)
    return await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id=f"wait-{state.revision}",
            actor_id=actor,
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="wait",
            wait_trigger=WaitTrigger.model_validate(declaration(**changes)),
        ),
        authenticated_actor_id=actor,
    )


async def resume(cid: str, play: PlayService, *, cancel: bool = False) -> CombatResult:
    state = await state_of(cid, play)
    return await CombatService(play).execute(
        cid,
        ResumeInterruptedTurn(
            id=f"resume-{state.revision}",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            cancel=cancel,
        ),
        authenticated_actor_id="a",
    )


async def armed(tmp_path: Path) -> tuple[str, PlayService]:
    """A declared unarmed Wait, with `a` mid-punch and the encounter paused."""
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice(())
    await wait(cid, play, "a")
    await declare(cid, play)
    result = await action(cid, play, "a", "punch", hands=("right-hand",), enter=True)
    assert isinstance(result, CombatResult) and result.code == "combat.wait_triggered"
    assert play.rng.exhausted()
    return cid, play


def test_wait_declares_exactly_one_kind_of_reaction() -> None:
    with pytest.raises(ValueError, match="either a weapon or an unarmed attack"):
        WaitTrigger.model_validate(declaration(item_id="sword-b"))
    with pytest.raises(ValueError, match="either a weapon or an unarmed attack"):
        WaitTrigger.model_validate({"action": "attack", "reaction_target_id": "a"})
    with pytest.raises(ValueError, match="ordinary or All-Out Attack"):
        WaitTrigger.model_validate(declaration(reaction="ready"))
    with pytest.raises(ValueError, match="ordinary or All-Out Attack"):
        WaitTrigger.model_validate(declaration(stop_thrust=True, target_id="a"))
    with pytest.raises(ValueError, match="arm-lock reaction names its grapple"):
        WaitTrigger.model_validate(declaration(unarmed={**PUNCH, "grip_id": "grip"}))


async def test_declared_reaction_resolves_before_the_interrupted_attack(
    tmp_path: Path,
) -> None:
    cid, play = await armed(tmp_path)
    paused = (await state_of(cid, play)).encounters[0]
    interrupt = paused.wait_interrupt
    assert interrupt is not None and (interrupt.waiter_id, interrupt.actor_id) == ("b", "a")
    # The attacker's close-combat entry happens before the pause, exactly as a step does.
    assert paused.close_pairs == (("a", "b"),)
    assert not any(p.maneuver_state.wait for p in paused.participants)

    # The pause holds the attack; DX 10 rolls only once the target settles its defense.
    play.rng = RecordedDice(())
    await action(cid, play, "b", "punch", target="a", hands=("right-hand",))
    assert play.rng.exhausted()
    # DX 10 with no posture or shock modifier: 9 on 3d hits. ST 10 thrust is 1d-2 and a
    # punch is thrust-1, so the damage die 4 yields one crushing point.
    play.rng = RecordedDice((3, 3, 3, 4))
    await defend(cid, play)
    assert play.rng.exhausted()
    struck = (await state_of(cid, play)).encounters[0]
    assert [(t.action, t.actor_id, t.won) for t in struck.unarmed_history] == [("punch", "b", True)]
    assert struck.current_actor_id == "a"
    ready = struck.wait_interrupt
    assert ready is not None and ready.ready and not ready.reacting

    play.rng = RecordedDice((5, 5, 5))
    await resume(cid, play)
    await defend(cid, play)
    assert play.rng.exhausted()
    after = await state_of(cid, play)
    finished = after.encounters[0]
    assert [(t.action, t.actor_id) for t in finished.unarmed_history] == [
        ("punch", "b"),
        ("punch", "a"),
    ]
    assert finished.wait_interrupt is None and finished.current_actor_id == "b"
    # One point lands through no DR; 15 on 3d misses and never rolls damage.
    pools = {p.id: p for p in after.resources.pools}
    assert (pools["hp:a"].current, pools["hp:b"].current) == (9, 10)
    # The waiter spent the Wait, not a second turn; only the attacker's turn advanced.
    assert pools["hp:a"].injury is not None and pools["hp:b"].injury is not None
    assert (pools["hp:a"].injury.turn, pools["hp:b"].injury.turn) == (2, 1)


async def test_resumed_attack_does_not_enter_close_combat_twice(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    play.rng = RecordedDice((5, 5, 5))
    await action(cid, play, "b", "punch", target="a", hands=("right-hand",))
    await defend(cid, play)
    entered = (await state_of(cid, play)).encounters[0]
    positions = {p.actor_id: p.position for p in entered.participants}
    assert positions["a"] == positions["b"]
    play.rng = RecordedDice((5, 5, 5))
    await resume(cid, play)
    await defend(cid, play)
    assert play.rng.exhausted()
    resumed = (await state_of(cid, play)).encounters[0]
    assert {p.actor_id: p.position for p in resumed.participants} == positions
    assert resumed.close_pairs == (("a", "b"),)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"value": "kick", "hands": ()}, "match its recorded declaration"),
        ({"hands": ("left-hand",)}, "match its recorded declaration"),
        ({"skill": "skill:boxing"}, "match its recorded declaration"),
        ({"location": "neck"}, "match its recorded declaration"),
        ({"enter": True}, "match its recorded declaration"),
    ],
)
async def test_reaction_that_leaves_the_declaration_never_rolls(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    cid, play = await armed(tmp_path)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    arguments: dict[str, object] = {
        "value": "punch",
        "target": "a",
        "hands": ("right-hand",),
        **changes,
    }
    with pytest.raises(ValidationError, match=message):
        await action(cid, play, "b", **arguments)  # type: ignore[arg-type]
    assert await state_of(cid, play) == before


async def test_only_the_waiter_may_act_inside_the_paused_turn(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="Resolve the interrupted Wait"):
        await action(cid, play, "a", "punch", hands=("left-hand",))
    with pytest.raises(ConflictError, match="No interrupted turn is ready"):
        await resume(cid, play)
    assert await state_of(cid, play) == before


async def test_armed_reaction_cannot_answer_an_unarmed_declaration(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="declared Wait reaction is an unarmed attack"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="armed-reaction",
                actor_id="b",
                expected_revision=before.revision,
                encounter_id="fight",
                maneuver="move",
            ),
            authenticated_actor_id="b",
        )
    assert await state_of(cid, play) == before


async def test_waiter_may_decline_and_the_attack_still_resumes(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    state = await state_of(cid, play)
    play.rng = RecordedDice(())
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="decline",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="b",
    )
    declined = (await state_of(cid, play)).encounters[0]
    assert declined.unarmed_history == ()
    ready = declined.wait_interrupt
    assert ready is not None and ready.ready
    play.rng = RecordedDice((3, 3, 3, 5))
    await resume(cid, play)
    await defend(cid, play)
    assert play.rng.exhausted()

    after = await state_of(cid, play)
    assert [t.actor_id for t in after.encounters[0].unarmed_history] == ["a"]
    assert after.encounters[0].wait_interrupt is None


async def test_cancelled_resume_spends_the_interrupted_turn(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    play.rng = RecordedDice((3, 3, 3, 2))
    await action(cid, play, "b", "punch", target="a", hands=("right-hand",))
    await defend(cid, play)
    assert play.rng.exhausted()
    play.rng = RecordedDice(())
    await resume(cid, play, cancel=True)
    assert play.rng.exhausted()
    after = await state_of(cid, play)
    cancelled = after.encounters[0]
    assert [t.actor_id for t in cancelled.unarmed_history] == ["b"]
    assert cancelled.wait_interrupt is None and cancelled.current_actor_id == "b"
    hp = {p.id: p.injury.turn for p in after.resources.pools if p.injury and p.id.startswith("hp:")}
    assert hp == {"hp:a": 2, "hp:b": 1}


async def test_reaction_and_its_defense_replay_after_restart(tmp_path: Path) -> None:
    cid, play = await armed(tmp_path)
    state = await state_of(cid, play)
    reaction = {
        "kind": "take_unarmed_turn",
        "id": "reaction",
        "actor_id": "b",
        "expected_revision": state.revision,
        "encounter_id": "fight",
        "action": "punch",
        "target_id": "a",
        "hands": ("right-hand",),
    }
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    first = await CombatService(restarted).execute(cid, reaction, authenticated_actor_id="b")
    assert restarted.rng.exhausted()
    replayed = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    replayed.rng = RecordedDice(())
    assert await CombatService(replayed).execute(cid, reaction, authenticated_actor_id="b") == first
    held = (await state_of(cid, replayed)).encounters[0]
    interrupt = held.wait_interrupt
    assert interrupt is not None and interrupt.reacting and held.pending_unarmed is not None
    replayed.rng = RecordedDice((3, 3, 3, 4))
    await defend(cid, replayed)
    assert replayed.rng.exhausted()
    settled = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    settled.rng = RecordedDice(())
    resolved = (await state_of(cid, settled)).encounters[0]
    ready = resolved.wait_interrupt
    assert ready is not None and ready.ready and not ready.reacting
    assert [t.actor_id for t in resolved.unarmed_history] == ["b"]


async def test_grappling_actor_may_wait_and_a_free_release_never_triggers(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice((3, 3, 3))
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    await defend(cid, play)
    assert play.rng.exhausted()
    held = (await state_of(cid, play)).encounters[0]
    grip = held.grips[0]
    play.rng = RecordedDice(())
    # A grappled fighter may still commit to a Wait; only the free hand is declarable.
    await declare(cid, play, actor="b", actor_id="a", unarmed={"action": "kick", "hands": ()})
    waiting = (await state_of(cid, play)).encounters[0]
    assert next(p for p in waiting.participants if p.actor_id == "b").maneuver_state.wait
    # Releasing a grip is free, so it is not the observable attack a Wait answers.
    await action(cid, play, "a", "release", grip=grip.id, hands=("left-hand",))
    assert play.rng.exhausted()
    after = (await state_of(cid, play)).encounters[0]
    assert after.wait_interrupt is None
    assert after.grips[0].hands == ("right-hand",)
    assert next(p for p in after.participants if p.actor_id == "b").maneuver_state.wait


async def test_undeclarable_unarmed_wait_is_refused_before_the_trigger(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice((3, 3, 3))
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    await defend(cid, play)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit free, usable hands"):
        await declare(cid, play, actor="a", actor_id="b", reaction_target_id="b")
    with pytest.raises(ValidationError, match="Skill does not support"):
        await declare(
            cid,
            play,
            actor="a",
            actor_id="b",
            reaction_target_id="b",
            unarmed={"action": "kick", "hands": (), "skill": "skill:judo"},
        )
    with pytest.raises(ValidationError, match="requires one declared foe"):
        await declare(cid, play, actor="a", actor_id="b", reaction_target_id=None)
    assert await state_of(cid, play) == before


async def test_stop_thrust_cannot_answer_a_close_combat_entry(tmp_path: Path) -> None:
    """A stop thrust rewards a closing charge, so entering close combat is refused."""
    from test_tactical import setup as tactical_setup

    cid, play = await tactical_setup(tmp_path)
    play.rng = RecordedDice(())
    for actor in ("a", "b", "c"):
        state = await state_of(cid, play)
        trigger = (
            {
                "actor_id": "a",
                "action": "attack",
                "reaction": "attack",
                "reaction_target_id": "a",
                "item_id": "sword-b",
                "mode_id": "thrust",
                "stop_thrust": True,
            }
            if actor == "b"
            else None
        )
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=f"prepare-{actor}",
                actor_id=actor,
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="wait" if actor == "b" else "do_nothing",
                wait_trigger=None if trigger is None else WaitTrigger.model_validate(trigger),
            ),
            authenticated_actor_id=actor,
        )
    before = await state_of(cid, play)
    with pytest.raises(ValidationError, match="stop thrust against close-combat entry"):
        await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    assert play.rng.exhausted()
    assert await state_of(cid, play) == before
