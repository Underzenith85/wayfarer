"""Independent B423-425/B429 procedure timing and persisted consequence cases."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_gurps_recovery import PROFILE, seed
from test_resources import engine

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.recovery import interrupt_tasks
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.medical import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
    apply_recovery,
)
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def patient(*, mortal: bool = False, hp: int = 5, deadline: int = 200) -> ResourceState:
    state = seed(-10 if not mortal else 10, hp)
    hit, fatigue = state.pools
    assert hit.injury is not None and fatigue.fatigue is not None
    if mortal:
        hit = hit.model_copy(
            update={
                "injury": hit.injury.model_copy(
                    update={
                        "mortal_wound": True,
                        "mortal_wound_due": 1800,
                    }
                )
            }
        )
    else:
        fatigue = fatigue.model_copy(
            update={
                "fatigue": fatigue.fatigue.model_copy(
                    update={
                        "heart_attack": True,
                        "heart_attack_deadline": deadline,
                        "unconscious": True,
                    }
                )
            }
        )
    return state.model_copy(update={"pools": (hit, fatigue)})


def advance(state: ResourceState, to: int) -> ResourceState:
    return engine().apply(
        state,
        Advance(id=f"time-{state.revision}", actor_id="a", expected_revision=state.revision, to=to),
        system=True,
    )


def start(state: ResourceState, context: CareContext, *, surgery: bool = False) -> ResourceState:
    return apply_recovery(
        state,
        BeginRecovery(
            id=f"start-{state.revision}",
            actor_id="b",
            expected_revision=state.revision,
            target_id="a",
            kind="stabilize" if surgery else "resuscitate",
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )[0]


def survival(state: ResourceState, dice: list[int]) -> ResourceState:
    return apply_recovery(
        state,
        BeginRecovery(
            id=f"survival-{state.revision}",
            actor_id="a",
            expected_revision=state.revision,
            target_id="a",
            kind="mortal-check",
        ),
        CareContext(PROFILE, 10),
        rng=RecordedDice(dice),
        system=True,
    )[0]


@pytest.mark.parametrize("hp", [5, -5])
def test_rescue_before_deadline_preserves_wounds_and_signed_fp(hp: int) -> None:
    context = CareContext(PROFILE, 10, skill=12)
    state = start(patient(hp=hp), context)
    assert state.recovery_tasks[0].due == 60
    with pytest.raises(ConflictError, match="recovery deadline"):
        advance(state, 61)
    state = advance(state, 60)
    command = FinishRecovery(
        id="finish",
        actor_id="b",
        expected_revision=state.revision,
        task_id=state.recovery_tasks[0].id,
    )
    state, result = apply_recovery(
        state, command, context, rng=RecordedDice([4, 4, 4]), system=True
    )
    assert result.resuscitated and result.check is not None and result.check.effective_target == 12
    assert state.pools[0].current == min(0, hp)
    assert state.pools[1].current == -10
    assert state.pools[1].fatigue is not None and state.pools[1].fatigue.unconscious
    assert apply_recovery(state, command, context, rng=RecordedDice([]), system=True) == (
        state,
        result,
    )
    after = advance(state, 201)
    assert after.pools[0].injury is not None and not after.pools[0].injury.dead


def test_failed_first_aid_attempt_can_retry_and_deadline_wins() -> None:
    context = CareContext(PROFILE, 10, skill=12, treatment_modifier=-4)
    state = start(patient(), context)
    state = advance(state, 60)
    state, result = apply_recovery(
        state,
        FinishRecovery(
            id="failed",
            actor_id="b",
            expected_revision=state.revision,
            task_id=state.recovery_tasks[0].id,
        ),
        context,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert not result.resuscitated
    assert result.check is not None and result.check.effective_target == 8
    state = start(state, context)
    state = advance(state, 70)
    state = state.model_copy(
        update={"recovery_tasks": interrupt_tasks(state.recovery_tasks, frozenset({"b"}), 70)}
    )
    state, result = apply_recovery(
        state,
        FinishRecovery(
            id="interrupted",
            actor_id="b",
            expected_revision=state.revision,
            task_id=state.recovery_tasks[-1].id,
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "interrupted" and not result.resuscitated
    state = advance(state, 200)
    assert state.pools[0].injury is not None and state.pools[0].injury.dead


def test_resuscitation_rejects_low_tl_and_insufficient_time() -> None:
    context = CareContext(PROFILE, 10, skill=12)
    with pytest.raises(ValidationError, match="TL7"):
        start(patient(), replace(context, technology_level=6))
    with pytest.raises(ValidationError, match="before the fatal deadline"):
        start(patient(deadline=60), context)


@pytest.mark.parametrize("hp,penalty", [(-29, 0), (-30, -2), (-40, -4)])
def test_stabilization_waits_for_two_survival_checks(hp: int, penalty: int) -> None:
    context = CareContext(PROFILE, 10, skill=16, technology_level=6, surgical_facility=True)
    state = start(patient(mortal=True, hp=hp), context, surgery=True)
    assert state.recovery_tasks[0].due == 3600
    assert state.recovery_tasks[0].treatment_modifier == penalty
    with pytest.raises(ConflictError, match="survival check"):
        advance(state, 3600)
    state = survival(advance(state, 1800), [3, 3, 3])
    state = advance(state, 3600)
    command = FinishRecovery(
        id="surgery",
        actor_id="b",
        expected_revision=state.revision,
        task_id=state.recovery_tasks[0].id,
    )
    with pytest.raises(ValidationError, match="survival check"):
        apply_recovery(state, command, context, rng=RecordedDice([]), system=True)
    state = survival(state, [3, 3, 3])
    command = command.model_copy(update={"expected_revision": state.revision})
    state, result = apply_recovery(
        state, command, context, rng=RecordedDice([3, 3, 3]), system=True
    )
    assert result.stabilized and state.pools[0].current == hp
    assert state.pools[0].injury is not None
    assert not state.pools[0].injury.mortal_wound and state.pools[0].injury.unconscious
    assert state.pools[0].injury.mortal_wound_due is None


def test_mortality_failure_and_miraculous_survival_retire_pending_surgery() -> None:
    context = CareContext(PROFILE, 10, skill=12, surgical_facility=True)
    for dice, dead in [([4, 4, 4], True), ([1, 1, 1], False)]:
        state = start(patient(mortal=True, hp=-20), context, surgery=True)
        state = survival(advance(state, 1800), dice)
        assert state.pools[0].injury is not None and state.pools[0].injury.dead is dead
        assert state.recovery_tasks[0].settled
        advance(state, 10000)


def test_new_mortal_wound_records_its_clock_deadline() -> None:
    state, _ = apply_injury(
        seed(hp=-9),
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([4, 4, 3]),
        system=True,
    )
    assert state.pools[0].injury is not None and state.pools[0].injury.mortal_wound
    assert state.pools[0].injury.mortal_wound_due == 1800


def test_failed_surgery_records_damage_and_cumulative_retry_penalty() -> None:
    context = CareContext(PROFILE, 10, skill=16, technology_level=6, surgical_facility=True)
    state = start(patient(mortal=True, hp=-11), context, surgery=True)
    state = survival(advance(state, 1800), [3, 3, 3])
    state = survival(advance(state, 3600), [3, 3, 3])
    state, result = apply_recovery(
        state,
        FinishRecovery(
            id="failed-surgery",
            actor_id="b",
            expected_revision=state.revision,
            task_id=state.recovery_tasks[0].id,
        ),
        context,
        rng=RecordedDice([6, 6, 5, 1, 1, 1]),
        system=True,
    )
    assert not result.stabilized and result.hp_recovered == -3
    assert state.pools[0].current == -14
    assert any(e.id.startswith("injury:medical-hp:") for e in state.events)
    state = start(state, context, surgery=True)
    assert state.recovery_tasks[-1].treatment_modifier == -2


async def test_survival_authority_sqlite_reconnect_and_no_reroll(tmp_path: Path) -> None:
    from test_medical_service import setup

    from wayfarer.orchestration.medical import CareEnvironment, MedicalService
    from wayfarer.orchestration.play import PlayService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    cid, play, service = await setup(tmp_path)
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    resources = state.resources
    pools = tuple(
        p.model_copy(update={"current": -11, "injury": patient(mortal=True).pools[0].injury})
        if p.id == "hp:a"
        else p
        for p in resources.pools
    )
    resources = resources.model_copy(update={"pools": pools, "game_time": 1800})
    state = state.model_copy(update={"resources": resources})
    campaign["id"] = cid = str(uuid4())
    state = state.model_copy(update={"campaign_id": cid})
    actor = state.actors[0]
    assert actor.approval is not None
    state = state.model_copy(
        update={
            "actors": (
                actor.model_copy(
                    update={
                        "approval": actor.approval.model_copy(update={"campaign_id": cid}),
                    }
                ),
            )
        }
    )
    campaign["play_json"] = state.model_dump_json()
    state = state.model_copy(
        update={
            "approvals": tuple(a.model_copy(update={"campaign_id": cid}) for a in state.approvals)
        }
    )
    campaign["play_json"] = state.model_dump_json()
    await play.store.insert(campaign)
    command = BeginRecovery(
        id="survive", actor_id="a", target_id="a", kind="mortal-check", expected_revision=0
    )
    with pytest.raises(ValidationError, match="authenticated"):
        await service.execute(cid, command, authenticated_actor_id="b")
    play.rng = RecordedDice([3, 3, 3])
    first = await service.execute(cid, command, authenticated_actor_id="a")
    reopened = PlayService(
        AsyncSQLiteStore(tmp_path / "medical.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    service = MedicalService(reopened, lambda *_args: CareEnvironment())
    assert await service.execute(cid, command, authenticated_actor_id="a") == first
    restored = reopened._load(await reopened.store.read(cid))
    hit = next(p for p in restored.resources.pools if p.id == "hp:a")
    assert hit.injury is not None and hit.injury.mortal_wound_due == 3600
