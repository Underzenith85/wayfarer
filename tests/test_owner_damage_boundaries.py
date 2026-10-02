"""Owner damage timing, fixed prerequisites and current authority boundaries."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_multiple_symptoms import EFFECTS, cyclic_selection
from test_opponent_attack_host import begin as begin_opponent
from test_opponent_attack_host import fixture as opponent_fixture
from test_owner_damage_host import begin, choose, fixture
from test_symptom_attribute_consumers import attribute_penalty

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, binding
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.traits.composed_sources import source_history
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamageOutcome
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


@pytest.mark.parametrize(("points", "seconds"), [(15, 3600), (30, 1800), (60, 600)])
@pytest.mark.parametrize("secret", [False, True])
async def test_exact_microsecond_cooldown_and_unrolled_secret_declaration(
    tmp_path: Path, points: int, seconds: int, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, points=points, dr=10)
    origin = real_play_clock(play._load(await play.store.read(cid))).observed_at_us
    assert origin is not None
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    play.rng = RecordedDice((2, 2, 3, 3))
    await choose(play, cid)
    state = play._load(await play.store.read(cid))
    deadline = 900001 + seconds * 1_000_000
    assert real_play_clock(state).cooldowns[0].available_at_microseconds == deadline
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="idle",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    source = source_history(play._load(await play.store.read(cid)).resources)[-1]
    await declare(play, cid, source.id, identifier="second-attack")
    now[0] = origin + deadline - 1
    play.rng = RecordedDice((2, 2, 2) + (() if secret else (1, 1)))
    await begin(
        play, cid, identifier="second-prepare", secret=secret, principal="gm" if secret else "bob"
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="cooling down"):
        await choose(play, cid, identifier="early")
    assert await play.store.read(cid) == before
    now[0] += 1
    if secret:
        play.rng = RecordedDice((2, 2, 3, 3, 3, 3))
        _, result = await choose(play, cid, identifier="exact")
        # Secret output is opaque, but the committed GM trace proves three rolls.
        assert result.luck is None and result.damage_json is None
        after = play._load(await play.store.read(cid))
        assert snapshot(after).luck.receipts[-1].attempts == ((2, 2), (3, 3), (3, 3))
        assert snapshot(after).luck.receipts[-1].chosen_index == 1
        assert (
            real_play_clock(after).cooldowns[0].available_at_microseconds
            == deadline + seconds * 1_000_000
        )
    else:
        with pytest.raises(ValidationError, match="when this original was rolled"):
            await choose(play, cid, identifier="waited")
        await choose(play, cid, identifier="accept", luck=False)
    assert play.rng.exhausted()


async def test_pause_resume_keeps_damage_original_and_cooldown_anchor(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((2, 2, 2, 1, 2))
    await begin(play, cid)
    pending = snapshot(play._load(await play.store.read(cid))).pending
    play.rng = RecordedDice(())
    for identifier, running in (("pause", False), ("resume", True)):
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id=identifier, actor_id="gm", expected_revision=state.revision, running=running
            ),
            principal_id="gm",
        )
        assert snapshot(play._load(await play.store.read(cid))).pending == pending
    await choose(play, cid, luck=False)
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_control_stale_revision_and_retry_recheck(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert pending
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="stale",
                actor_id="a",
                expected_revision=state.revision - 1,
                pending_id=pending.id,
                choice="use-luck",
            ),
            principal_id="alice",
        )
    command, result = await choose(play, cid, luck=False)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"actor_ids": ()}) if m.principal_id == "alice" else m
                    for m in state.members
                )
            }
        ),
    )
    before = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == before and result.damage_json
    assert next(p.current for p in play._load(before).resources.pools if p.id == "hp:b") == 8


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_lost_attacker_approval_preserves_delivered_original_for_gm_acceptance(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 1, 2))
    await begin(play, cid)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                    for a in state.actors
                )
            }
        ),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await choose(play, cid)
    assert await play.store.read(cid) == before
    command, result = await choose(play, cid, luck=False, principal="gm")
    assert result.damage_json
    consequence = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert (
        consequence.dice == (1, 2)
        and consequence.combat
        and consequence.combat.injury
        and consequence.combat.injury.attack.dice == (2, 2, 2)
    )
    after = await play.store.read(cid)
    assert next(p.current for p in play._load(after).resources.pools if p.id == "hp:b") == 7
    assert await TaskService(play).execute(cid, command, principal_id="gm") == result
    assert await play.store.read(cid) == after


async def test_changed_target_hp_resumes_original_without_restoring_prior_hp(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)

    def changed(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 9}) if p.id == "hp:b" else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )

    await change(play, cid, changed)
    play.rng = RecordedDice(())
    command, result = await choose(play, cid, luck=False)
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.dice == (1, 1) and outcome.combat and outcome.combat.injury
    assert outcome.combat.injury.hp_before == 9 and outcome.combat.injury.hp_after == 7
    after = await play.store.read(cid)
    assert snapshot(play._load(after)).pending is None and play.rng.exhausted()
    assert await TaskService(play).execute(cid, command, principal_id="alice") == result
    assert await play.store.read(cid) == after


@pytest.mark.parametrize(
    ("table", "dice", "expected"), [((1, 1, 1), (1, 1), 18), ((2, 2, 2), (), 12)]
)
async def test_critical_delivery_is_fixed_and_maximum_damage_has_no_luck_roll(
    tmp_path: Path, table: tuple[int, int, int], dice: tuple[int, ...], expected: int
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((1, 1, 1) + table + dice + ((2, 2, 2) if not dice else ()))
    await begin(play, cid)
    if dice:
        state = play._load(await play.store.read(cid))
        pending = snapshot(state).pending
        assert pending
        play.rng = RecordedDice((2, 2, 3, 3, 2, 2, 2))
        _, result = await choose(play, cid)
        assert result.damage_json
        injury = OwnerDamageOutcome.model_validate_json(result.damage_json).combat
        assert injury and injury.injury and injury.injury.critical_table == table
    after = play._load(await play.store.read(cid))
    assert snapshot(after).pending is None
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10 - expected
    assert bool(snapshot(after).luck.receipts) == bool(dice)
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_damage_drives_actual_cyclic_and_multiple_symptom_debts(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(
        tmp_path,
        backend,
        modifiers=(cyclic_selection(),) + EFFECTS,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    before = play._load(await play.store.read(cid))
    assert not before.resources.cyclic_attacks and not before.resources.symptom_debts
    play.rng = RecordedDice((2, 2, 4, 4, 2, 2, 2))
    await choose(play, cid)
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 2
    assert (
        len(after.resources.symptom_debts) == 1
        and sum(d.remaining for d in after.resources.symptom_debts) == 8
    )
    assert len(after.resources.symptom_effects) == 2 and acute_blindness(after.resources, "b")
    occurrence = after.resources.cyclic_attacks[0]
    assert (occurrence.damage_dice, occurrence.hp_debt, occurrence.remaining, occurrence.due) == (
        2,
        8,
        2,
        10,
    )
    assert binding(after.resources, occurrence.id) is not None
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_fatigue_damage_uses_current_signed_fp_and_hp_ledger(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, kind="fat")
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        ),
    )
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    before = play._load(await play.store.read(cid))
    assert next(p.current for p in before.resources.pools if p.id == "fp:b") == 2
    play.rng = RecordedDice((2, 2, 3, 3))
    _, result = await choose(play, cid)
    after = play._load(await play.store.read(cid))
    # B426: six fatigue damage spends the last2FP, then4FP+4HP, once.
    assert next(p.current for p in after.resources.pools if p.id == "fp:b") == -4
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 6
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.attack and outcome.attack.fatigue and outcome.attack.fatigue.hp_lost == 4
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combined_phase_keeps_attack_target_after_dx_loss_and_restart(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await opponent_fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((3, 3, 3))
    _, attack = await begin_opponent(play, cid, prepare_owner_damage=True)
    assert attack.check and attack.check.effective_target == 12 and attack.pending_id
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={"resources": attribute_penalty(state.resources, "a", "dx", level=10)}
        ),
    )
    state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id="join",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    joined = ChooseOpponentAttack(
        id=response.id,
        actor_id="b",
        expected_revision=state.revision,
        pending_id=attack.pending_id,
        choice="accept",
        response=response,
    )
    play.rng = RecordedDice((1, 1))
    result = await TaskService(play).execute(cid, joined, principal_id="bob")
    assert (
        result.status == "completed" and result.pending_id is None and result.check == attack.check
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    command, accepted = await choose(restarted, cid, luck=False)
    assert accepted.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(accepted.damage_json)
    assert outcome.combat and outcome.combat.injury and outcome.combat.injury.attack == attack.check
    assert outcome.dice == (1, 1) and outcome.combat.injury.hp_after == 8
    after = await restarted.store.read(cid)
    assert snapshot(restarted._load(after)).pending is None
    assert await TaskService(restarted).execute(cid, joined, principal_id="bob") == result
    assert await TaskService(restarted).execute(cid, command, principal_id="alice") == accepted
    assert await restarted.store.read(cid) == after == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_candidate_commit_leaves_original_clock_events_and_hp_intact(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((2, 2, 2, 3)), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose(failing, cid)
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    play.rng = RecordedDice((2, 2, 2, 3))
    await choose(play, cid)
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:b"
        )
        == 5
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_damage_preparation_does_not_disclose_current_luck_eligibility(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, with_luck=False)
    play.rng = RecordedDice((2, 2, 2, 1, 2))
    await begin(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Luck"):
        await choose(play, cid)
    assert await play.store.read(cid) == before and play.rng.exhausted()
    _, result = await choose(play, cid, luck=False)
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.dice == (1, 2) and outcome.combat and outcome.combat.injury
    assert outcome.combat.injury.hp_after == 7
    after = play._load(await play.store.read(cid))
    assert not snapshot(after).luck.receipts and not real_play_clock(after).cooldowns
