"""Real SQLite CAS/restart evidence for profile-selected recovery and shared time."""

from pathlib import Path

import pytest
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler

from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.catalog import RulesCatalog
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.recovery_types import FatigueStatus
from wayfarer.simulation.actions import ActionEngine, ActionRules, ActorSetup, Move, Wait
from wayfarer.simulation.medical import BeginRecovery, FinishRecovery
from wayfarer.simulation.resources import Owner, ResourceEngine, ResourceState

PROFILE = "gurps-basic-set-4e-2004"


async def setup(
    tmp_path: Path, *, stunned: bool = False
) -> tuple[str, PlayService, MedicalService]:
    compiler = profile_compiler(PROFILE)
    # An internal fixture binds the exact mechanics without changing registry gates.
    from test_statistics import profile_package

    catalog = RulesCatalog((profile_package(PROFILE),))
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(id="medical-test", version=1, fatigue_cost=0, maximum_wait=10000),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "medical.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a", proposal=CharacterProposal(draft=gurps_draft()), aware_of=("alley",)
            ),
        ),
    )
    pools = tuple(
        p.model_copy(
            update={
                "current": 5,
                "injury": InjuryStatus(profile_id="gurps-basic-set-4e-2004", stunned=stunned),
            }
        )
        if p.id == "hp:a"
        else p.model_copy(
            update={"current": 5, "fatigue": FatigueStatus(profile_id="gurps-basic-set-4e-2004")}
        )
        for p in state.resources.pools
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": pools})}
    )
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    return (
        initial["id"],
        play,
        MedicalService(play, lambda _play, _state, _target: CareEnvironment(food=True, water=True)),
    )


async def test_rest_wait_finish_retry_restart_and_atomic_replay(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    start = BeginRecovery(
        id="rest", actor_id="a", expected_revision=0, kind="rest", target_id="a", seconds=1200
    )
    assert (await service.execute(cid, start, authenticated_actor_id="a")).status == "pending"
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=1200),
        authenticated_actor_id="a",
    )
    finish = FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="rest")
    result = await service.execute(cid, finish, authenticated_actor_id="a")
    assert result.fp_recovered == 2 and result.hp_recovered == 0
    restarted = MedicalService(
        PlayService(play.store, play.engine, rng=RecordedDice([])),
        lambda *_args: (_ for _ in ()).throw(AssertionError("resolver must not rerun")),
    )
    assert await restarted.execute(cid, finish, authenticated_actor_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == 1200 and after.resources.revision == after.revision == 3
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 7
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 5


async def test_movement_interrupts_rest_retaining_only_completed_minutes(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    await service.execute(
        cid,
        BeginRecovery(
            id="rest", actor_id="a", expected_revision=0, kind="rest", target_id="a", seconds=1200
        ),
        authenticated_actor_id="a",
    )
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=900),
        authenticated_actor_id="a",
    )
    await play.execute(
        cid,
        Move(id="move", actor_id="a", expected_revision=2, destination_id="alley"),
        authenticated_actor_id="a",
    )
    result = await service.execute(
        cid,
        FinishRecovery(id="finish", actor_id="a", expected_revision=3, task_id="rest"),
        authenticated_actor_id="a",
    )
    assert result.status == "interrupted" and result.fp_recovered == 1
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            FinishRecovery(id="finish-again", actor_id="a", expected_revision=4, task_id="rest"),
            authenticated_actor_id="a",
        )


async def test_too_early_unauthorized_and_stale_commands_do_not_mutate(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    start = BeginRecovery(id="rest", actor_id="a", expected_revision=0, kind="rest", target_id="a")
    with pytest.raises(ValidationError):
        await service.execute(cid, start, authenticated_actor_id="b")
    await service.execute(cid, start, authenticated_actor_id="a")
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="not due"):
        await service.execute(
            cid,
            FinishRecovery(id="early", actor_id="a", expected_revision=1, task_id="rest"),
            authenticated_actor_id="a",
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            FinishRecovery(id="stale", actor_id="a", expected_revision=0, task_id="rest"),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before


async def test_stunned_healer_cannot_begin_treatment(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path, stunned=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Incapacitated"):
        await service.execute(
            cid,
            BeginRecovery(
                id="bandage",
                actor_id="a",
                target_id="a",
                kind="bandage",
                expected_revision=0,
                wound_id="injury:wound",
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before


async def test_heart_attack_deadline_uses_shared_clock_once(tmp_path: Path) -> None:
    from wayfarer.simulation.fatigue import ContinueExertion, apply_fatigue
    from wayfarer.simulation.resources import Advance

    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                for p in state.resources.pools
            )
        }
    )
    resources, result = apply_fatigue(
        resources,
        ContinueExertion(id="act", actor_id="a", expected_revision=0),
        ht=10,
        will=10,
        rng=RecordedDice([6, 6, 6, 5, 5, 5]),
        system=True,
    )
    assert not result.allowed
    fp = next(p for p in resources.pools if p.id == "fp:a")
    assert fp.current == -10 and fp.fatigue is not None and fp.fatigue.unconscious
    assert fp.fatigue.heart_attack_deadline == 200
    resources = play.engine.resources.apply(
        resources, Advance(id="before", actor_id="a", expected_revision=1, to=199), system=True
    )
    hp = next(p for p in resources.pools if p.id == "hp:a")
    assert hp.injury is not None and not hp.injury.dead
    command = Advance(id="deadline", actor_id="a", expected_revision=2, to=200)
    resources = play.engine.resources.apply(resources, command, system=True)
    hp = next(p for p in resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.dead
    assert len([e for e in resources.events if e.kind == "heart-attack-death"]) == 1
    assert play.engine.resources.apply(resources, command, system=True) == resources


async def test_rest_at_full_fp_cannot_bank_credit_against_future_cost(tmp_path: Path) -> None:
    from wayfarer.rules.checks import RecordedDice
    from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
    from wayfarer.simulation.medical import CareContext, apply_recovery
    from wayfarer.simulation.resources import Advance

    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": p.maximum}) if p.id == "fp:a" else p
                for p in state.pools
            )
        }
    )
    context = CareContext("gurps-basic-set-4e-2004", 10)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", target_id="a", kind="rest", seconds=600, expected_revision=0
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    with pytest.raises(ConflictError, match="deadline"):
        play.engine.resources.apply(
            state, Advance(id="past", actor_id="a", expected_revision=1, to=601), system=True
        )
    state = play.engine.resources.apply(
        state, Advance(id="due", actor_id="a", expected_revision=1, to=600), system=True
    )
    with pytest.raises(ConflictError, match="Settle"):
        apply_fatigue(
            state,
            FatigueCost(id="cost", actor_id="a", expected_revision=2, amount=3),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="rest"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_recovered == 0
    state, result_cost = apply_fatigue(
        state,
        FatigueCost(id="cost", actor_id="a", expected_revision=3, amount=3),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "fp:a") == 7 and result_cost.hp_lost == 0


async def test_partial_rest_accrues_before_exhaustion_cost_and_never_twice(tmp_path: Path) -> None:
    from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
    from wayfarer.simulation.medical import CareContext, apply_recovery
    from wayfarer.simulation.resources import Advance

    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p for p in state.pools
            )
        }
    )
    context = CareContext("gurps-basic-set-4e-2004", 10)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", target_id="a", kind="rest", seconds=1200, expected_revision=0
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = play.engine.resources.apply(
        state, Advance(id="partial", actor_id="a", expected_revision=1, to=601), system=True
    )
    assert next(p.current for p in state.pools if p.id == "fp:a") == 1
    state, cost = apply_fatigue(
        state,
        FatigueCost(id="cost", actor_id="a", expected_revision=2, amount=1),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert cost.hp_lost == 0
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=3, task_id="rest"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_recovered == 1
    assert next(p.current for p in state.pools if p.id == "fp:a") == 0
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest-2", actor_id="a", target_id="a", kind="rest", seconds=600, expected_revision=4
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = play.engine.resources.apply(
        state, Advance(id="due-2", actor_id="a", expected_revision=5, to=1201), system=True
    )
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish-2", actor_id="a", expected_revision=6, task_id="rest-2"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_recovered == 1 and next(p.current for p in state.pools if p.id == "fp:a") == 1


async def test_restricted_rest_accrual_preserves_continuous_day(tmp_path: Path) -> None:
    from wayfarer.simulation.medical import CareContext, apply_recovery
    from wayfarer.simulation.resources import Advance

    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(
                    update={
                        "current": 7,
                        "fatigue": FatigueStatus(
                            profile_id="gurps-basic-set-4e-2004", starvation=3
                        ),
                    }
                )
                if p.id == "fp:a"
                else p
                for p in state.pools
            )
        }
    )
    context = CareContext("gurps-basic-set-4e-2004", 10, food=True)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", target_id="a", kind="rest", seconds=86400, expected_revision=0
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = play.engine.resources.apply(
        state, Advance(id="hour", actor_id="a", expected_revision=1, to=3600), system=True
    )
    assert next(p.current for p in state.pools if p.id == "fp:a") == 7
    state = play.engine.resources.apply(
        state, Advance(id="day", actor_id="a", expected_revision=2, to=86400), system=True
    )
    assert next(p.current for p in state.pools if p.id == "fp:a") == 10
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=3, task_id="rest"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_recovered == 3 and next(p.current for p in state.pools if p.id == "fp:a") == 10


async def test_natural_healing_cannot_be_banked_for_future_injury(tmp_path: Path) -> None:
    from wayfarer.simulation.injury import Wound, apply_injury
    from wayfarer.simulation.medical import CareContext, apply_recovery
    from wayfarer.simulation.resources import Advance

    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": p.maximum}) if p.id == "hp:a" else p
                for p in state.pools
            )
        }
    )
    context = CareContext("gurps-basic-set-4e-2004", 10, food=True)
    state, _ = apply_recovery(
        state,
        BeginRecovery(id="day", actor_id="a", target_id="a", kind="natural", expected_revision=0),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = play.engine.resources.apply(
        state, Advance(id="due", actor_id="a", expected_revision=1, to=86400), system=True
    )
    with pytest.raises(ConflictError, match="Settle"):
        apply_injury(
            state,
            Wound(
                id="later",
                actor_id="a",
                expected_revision=2,
                basic_damage=2,
                resistance=0,
                damage_type="cr",
            ),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="day"),
        context,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.hp_recovered == 0
    state, _ = apply_injury(
        state,
        Wound(
            id="later",
            actor_id="a",
            expected_revision=3,
            basic_damage=2,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "hp:a") == 8
