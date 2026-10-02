"""B66/B271 unarmed damage choices commit actual strike injury and sequence state."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from test_gurps_melee import setup
from test_opponent_attack_routes import enroll, luck_source
from test_owner_damage_host import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamageOutcome
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.unarmed_damage_records import UnarmedDamagePending


async def fixture(path: Path, backend: str) -> tuple[str, PlayService]:
    definition, purchase = luck_source()
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    return cid, await enroll(path, backend, cid, original)


async def strike(
    play: PlayService,
    cid: str,
    *,
    action: Literal["punch", "kick"] = "punch",
    option: Literal["strong", "double"] | None = None,
) -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="strike",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            action=action,
            target_id="b",
            maneuver="all_out_attack" if option else "attack",
            attack_option=option,
            hands=("left-hand",) if action == "punch" else (),
            enter_close_combat=action == "punch",
        ),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
@pytest.mark.parametrize("action,injury", [("punch", 3), ("kick", 6)])
async def test_selected_unarmed_damage_changes_actual_hp_and_closes_one_paid_turn(
    tmp_path: Path, backend: str, secret: bool, action: Literal["punch", "kick"], injury: int
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await strike(play, cid, action=action, option="strong" if action == "kick" else None)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2) + (() if secret else (1,)))
    opening, first = await begin(play, cid, secret=secret, principal="gm" if secret else "b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, UnarmedDamagePending)
    assert pending.original == (None if secret else (1,)) and play.rng.exhausted()
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert state.encounters[0].current_actor_id == "a" and not state.encounters[0].unarmed_history
    assert state.resources.game_time == before.resources.game_time
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="b")
    choice = ChooseOwnerDamage(
        id="selected-strike",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(((1,) if secret else ()) + (3, 6) + ((4, 4, 4) if injury == 6 else ()))
    result = await TaskService(play).execute(cid, choice, principal_id="a")
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.current == 10 - injury and hp.injury and hp.injury.shock == min(4, injury)
    assert hp.injury.stunned == (injury == 6) and hp.injury.prone == (injury == 6)
    assert (
        after.encounters[0].current_actor_id == "b" and after.encounters[0].pending_unarmed is None
    )
    trace = after.encounters[0].unarmed_history[0]
    # ST10 thrust1d-2; untrained punch-1, or All-Out Strong kick+2.
    assert trace.damage_dice == (6,) and trace.basic_damage == injury and trace.injury == injury
    assert trace.checks[0].dice == (2, 2, 2) and len(trace.checks) == 1
    assert len(after.encounters[0].unarmed_history) == len(snapshot(after).luck.receipts) == 1
    assert play.rng.exhausted() and snapshot(after).pending is None
    if secret:
        assert result.damage_json is None and result.luck is None
    else:
        assert result.damage_json and OwnerDamageOutcome.model_validate_json(
            result.damage_json
        ).dice == (6,)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, choice, principal_id="a") == result
    assert (
        await TaskService(restarted).execute(cid, opening, principal_id="gm" if secret else "b")
        == first
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
    with pytest.raises(ConflictError):
        await TaskService(restarted).execute(
            cid,
            choice.model_copy(update={"id": "late", "expected_revision": after.revision}),
            principal_id="a",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_double_punch_settles_each_real_strike_then_advances_once(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await fixture(tmp_path, backend)
    await strike(play, cid, option="double")
    for index in range(2):
        play.rng = RecordedDice((3, 3, 3, 1))
        await begin(play, cid, identifier="prepare-" + str(index), principal="b")
        state = play._load(await play.store.read(cid))
        pending = snapshot(state).pending
        assert isinstance(pending, UnarmedDamagePending)
        command = ChooseOwnerDamage(
            id="select-" + str(index),
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        )
        if index == 0:
            play.rng = RecordedDice((3, 6))
        else:
            play.rng = RecordedDice(())
            with pytest.raises(ValidationError, match="cooling down"):
                await TaskService(play).execute(cid, command, principal_id="a")
            command = command.model_copy(update={"choice": "accept"})
        await TaskService(play).execute(cid, command, principal_id="a")
        state = play._load(await play.store.read(cid))
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 7
        assert len(state.encounters[0].unarmed_history) == index + 1
        assert state.encounters[0].current_actor_id == ("a" if index == 0 else "b")
        assert (state.encounters[0].pending_unarmed is not None) == (index == 0)
        assert play.rng.exhausted()
    assert len(snapshot(state).luck.receipts) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_kick_uses_current_target_injury_without_rescoring_closed_delivery(
    tmp_path: Path, backend: str
) -> None:
    from test_combat_sensory_authority import change
    from test_owner_damage_current_target import current_target

    cid, play = await fixture(tmp_path, backend)
    await strike(play, cid, action="kick", option="strong")
    play.rng = RecordedDice((2, 2, 2, 1))
    await begin(play, cid, principal="b")
    await change(play, cid, lambda state: current_target(play, state))
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, UnarmedDamagePending)
    play.rng = RecordedDice((3, 6, 4, 4, 4))
    result = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="current-kick",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.combat and outcome.combat.unarmed
    assert outcome.combat.unarmed.checks == pending.preparation.inputs.trace.checks
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.current == 2 and hp.injury and not hp.injury.stunned and not hp.injury.prone
    assert next(p.current for p in after.resources.pools if p.id == "fp:b") == 7
    assert (
        next(p.posture for p in after.encounters[0].participants if p.actor_id == "b") == "kneeling"
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unarmed_original_acceptance_survives_lost_owner_approval(
    tmp_path: Path, backend: str
) -> None:
    from test_opponent_attack_routes import revoke_attacker

    from wayfarer.errors import ValidationError

    cid, play = await fixture(tmp_path, backend)
    await strike(play, cid)
    play.rng = RecordedDice((3, 3, 3, 6))
    await begin(play, cid, principal="b")
    await revoke_attacker(play, cid)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, UnarmedDamagePending)
    command = ChooseOwnerDamage(
        id="revoked-strike",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="approval"):
        await TaskService(play).execute(cid, command, principal_id="a")
    result = await TaskService(play).execute(
        cid, command.model_copy(update={"choice": "accept"}), principal_id="gm"
    )
    assert result.damage_json
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 7
    assert next(a for a in after.actors if a.actor_id == "a").approval is None
    assert after.encounters[0].current_actor_id == "b" and play.rng.exhausted()
