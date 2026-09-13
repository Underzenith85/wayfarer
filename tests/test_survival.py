"""Independently entered B426-427 survival boundaries and durable receipts."""

from typing import Final

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.rules.types.survival import (
    SurvivalStatus,
    SurvivalTask,
    interrupt_survival_tasks,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.survival import (
    BeginForage,
    BeginSleep,
    BeginSurvival,
    FinishSurvivalActivity,
    ForagingContext,
    SettleSurvival,
    SurvivalContext,
    begin_foraging,
    begin_sleep,
    begin_survival,
    finish_foraging,
    finish_sleep,
    settle_survival,
)
from wayfarer.engine.simulation.resources import (
    Advance,
    Consume,
    Item,
    Owner,
    Pool,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"


def seed(*, fp: int = 10, hp: int = 10, party: bool = False) -> ResourceState:
    actors = ("a", "b") if party else ("a",)
    return ResourceState(
        owners=tuple(Owner(actor_id=actor, capacity=100) for actor in actors),
        pools=tuple(
            pool
            for actor in actors
            for pool in (
                Pool(
                    id="hp:" + actor,
                    current=hp,
                    maximum=10,
                    injury=InjuryStatus(profile_id=PROFILE),
                ),
                Pool(
                    id="fp:" + actor,
                    current=fp,
                    maximum=10,
                    fatigue=FatigueStatus(profile_id=PROFILE),
                ),
            )
        ),
    )


def context(**changes: object) -> SurvivalContext:
    values: dict[str, object] = {"profile_id": PROFILE, "ht": 10, "will": 10}
    values.update(changes)
    return SurvivalContext(**values)  # type: ignore[arg-type]


def start(state: ResourceState, ctx: SurvivalContext | None = None) -> ResourceState:
    state, _ = begin_survival(
        state,
        BeginSurvival(id="survival", actor_id="a", expected_revision=state.revision),
        ctx or context(),
        system=True,
    )
    return state


def test_meals_and_water_settle_once_per_eight_hour_interval_after_restart() -> None:
    state = seed().model_copy(
        update={
            "items": (
                Item(id="food", definition_id="ration", owner_id="a", quantity=2),
                Item(id="water", definition_id="water-quart", owner_id="a", quantity=2),
            )
        }
    )
    ctx = context(meal_item_ids=("food",), water_item_ids=("water",))
    state = start(state, ctx).model_copy(update={"game_time": 28800})
    command = SettleSurvival(id="interval-1", actor_id="a", expected_revision=1)
    state, result = settle_survival(
        state, command, ctx, rng=RecordedDice([]), system=True
    )
    assert (result.meals_consumed, result.water_consumed, result.fp_lost) == (1, 2, 0)
    assert next(item for item in state.items if item.id == "food").quantity == 1
    assert {item.id for item in state.expended_items} == {"water", "spent:interval-1:meal:food"}

    restored = ResourceState.model_validate_json(state.model_dump_json())
    replay, repeated = settle_survival(
        restored, command, ctx, rng=RecordedDice([]), system=True
    )
    assert replay == restored and repeated == result
    assert next(item for item in replay.items if item.id == "food").quantity == 1


def test_starvation_dehydration_and_zero_fp_spill_to_hp() -> None:
    state = start(seed(fp=1)).model_copy(update={"game_time": 28800})
    state, result = settle_survival(
        state,
        SettleSurvival(id="missed", actor_id="a", expected_revision=1),
        context(),
        rng=RecordedDice([]),
        system=True,
    )
    fp = next(pool for pool in state.pools if pool.id == "fp:a")
    hp = next(pool for pool in state.pools if pool.id == "hp:a")
    assert (fp.current, hp.current, result.fp_lost, result.hp_lost) == (-1, 9, 2, 1)
    assert fp.fatigue is not None
    assert (fp.fatigue.starvation, fp.fatigue.dehydration) == (1, 1)


def test_water_below_one_quart_adds_daily_fp_and_hp_loss() -> None:
    status = SurvivalStatus(
        actor_id="a",
        started=0,
        next_meal_due=200000,
        next_water_due=86400,
        awake_since=0,
        next_sleep_due=200000,
        water_day_started=0,
        water_quarts_required=2,
    )
    state = seed().model_copy(update={"game_time": 86400, "survival": (status,)})
    state, result = settle_survival(
        state,
        SettleSurvival(id="dry-day", actor_id="a", expected_revision=0),
        context(),
        rng=RecordedDice([]),
        system=True,
    )
    assert (result.fp_lost, result.hp_lost) == (2, 1)
    assert next(pool for pool in state.pools if pool.id == "fp:a").current == 8
    assert next(pool for pool in state.pools if pool.id == "hp:a").current == 9


def test_missed_sleep_drowsiness_is_persistent_and_failed_will_forces_sleep() -> None:
    fatigue = FatigueStatus(profile_id=PROFILE, sleep=4)
    status = SurvivalStatus(
        actor_id="a",
        started=0,
        next_meal_due=100000,
        next_water_due=100000,
        awake_since=0,
        next_sleep_due=57600,
        water_day_started=0,
    )
    state = seed(fp=6).model_copy(
        update={
            "game_time": 57600,
            "survival": (status,),
            "pools": tuple(
                pool.model_copy(update={"fatigue": fatigue}) if pool.id == "fp:a" else pool
                for pool in seed(fp=6).pools
            ),
        }
    )
    state, _ = settle_survival(
        state,
        SettleSurvival(id="sleep-loss", actor_id="a", expected_revision=0),
        context(),
        rng=RecordedDice([]),
        system=True,
    )
    due = state.survival[0].next_drowsiness_due
    assert due == 64800
    state = state.model_copy(update={"game_time": due})
    state, succeeded = settle_survival(
        state,
        SettleSurvival(id="drowse", actor_id="a", expected_revision=1),
        context(active=False),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert succeeded.drowsiness_check is not None
    assert sum(modifier.value for modifier in check_modifiers(state, "a", "dx")) == -2
    due = state.survival[0].next_drowsiness_due
    state = state.model_copy(update={"game_time": due})
    state, failed = settle_survival(
        state,
        SettleSurvival(id="drowse-again", actor_id="a", expected_revision=2),
        context(active=False),
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert failed.drowsiness_check is not None and state.survival[0].forced_asleep

    with pytest.raises(ValidationError, match="sleeping actor"):
        begin_foraging(
            state,
            BeginForage(id="no", actor_id="a", expected_revision=3, mode="travel"),
            forage_context(),
            system=True,
        )


def test_full_sleep_recovers_sleep_and_ordinary_fatigue_and_short_sleep_shifts_day() -> None:
    fatigue = FatigueStatus(profile_id=PROFILE, sleep=2)
    state = start(seed(fp=5)).model_copy(
        update={
            "pools": tuple(
                pool.model_copy(update={"fatigue": fatigue}) if pool.id == "fp:a" else pool
                for pool in seed(fp=5).pools
            )
        }
    )
    state, _ = begin_sleep(
        state,
        BeginSleep(id="sleep", actor_id="a", expected_revision=1, seconds=32400),
        context(),
        system=True,
    )
    state = state.model_copy(update={"game_time": 32400})
    state, result = finish_sleep(
        state,
        FinishSurvivalActivity(
            id="wake", actor_id="a", expected_revision=2, task_id="sleep"
        ),
        context(),
        system=True,
    )
    fp = next(pool for pool in state.pools if pool.id == "fp:a")
    assert result.fp_recovered == 5 and fp.current == 10
    assert fp.fatigue is not None and fp.fatigue.sleep == 0
    assert state.survival[0].next_sleep_due == 90000

    fresh = start(seed())
    fresh, _ = begin_sleep(
        fresh,
        BeginSleep(id="nap", actor_id="a", expected_revision=1, seconds=21600),
        context(),
        system=True,
    )
    fresh = fresh.model_copy(update={"game_time": 21600})
    fresh, _ = finish_sleep(
        fresh,
        FinishSurvivalActivity(id="up", actor_id="a", expected_revision=2, task_id="nap"),
        context(),
        system=True,
    )
    assert fresh.survival[0].next_sleep_due == 64800


def test_day_of_rest_consumes_three_meals_and_ample_water_to_recover_deprivation() -> None:
    fatigue = FatigueStatus(profile_id=PROFILE, starvation=3, dehydration=2)
    state = start(seed(fp=5)).model_copy(
        update={
            "items": (
                Item(id="meals", definition_id="ration", owner_id="a", quantity=3),
                Item(id="water", definition_id="water-quart", owner_id="a", quantity=2),
            ),
            "pools": tuple(
                pool.model_copy(update={"fatigue": fatigue}) if pool.id == "fp:a" else pool
                for pool in seed(fp=5).pools
            ),
        }
    )
    ctx = context(meal_item_ids=("meals",), water_item_ids=("water",))
    state, _ = begin_sleep(
        state,
        BeginSleep(id="rest-day", actor_id="a", expected_revision=1, seconds=86400),
        ctx,
        system=True,
    )
    index = 0
    while state.survival[0].next_due <= 86400:
        due = state.survival[0].next_due
        state = state.model_copy(update={"game_time": due})
        state, _ = settle_survival(
            state,
            SettleSurvival(
                id=f"rest-interval:{index}",
                actor_id="a",
                expected_revision=state.revision,
            ),
            ctx,
            rng=RecordedDice([]),
            system=True,
        )
        index += 1
    state = state.model_copy(update={"game_time": 86400})
    command = FinishSurvivalActivity(
        id="finish-rest",
        actor_id="a",
        expected_revision=state.revision,
        task_id="rest-day",
    )
    state, result = finish_sleep(state, command, ctx, system=True)
    fp = next(pool for pool in state.pools if pool.id == "fp:a")
    assert (result.meals_consumed, result.water_consumed, result.fp_recovered) == (3, 2, 5)
    assert fp.current == 10 and fp.fatigue is not None
    assert (fp.fatigue.starvation, fp.fatigue.dehydration) == (0, 0)
    restored = ResourceState.model_validate_json(state.model_dump_json())
    assert finish_sleep(restored, command, ctx, system=True) == (restored, result)


def forage_context(*, party: bool = False) -> ForagingContext:
    return ForagingContext(
        profile_id=PROFILE,
        plant_skill=12,
        animal_skill=12,
        animal_method="missile",
        terrain_modifier=0,
        ration_definition_id="ration",
        supply_owner_id="a",
        party_ht=(("a", 10), ("b", 10)) if party else (("a", 10),),
    )


def test_foraging_is_time_bound_deterministic_and_cannot_mint_on_retry() -> None:
    state, pending = begin_foraging(
        seed(),
        BeginForage(id="forage", actor_id="a", expected_revision=0, mode="travel"),
        forage_context(),
        system=True,
    )
    assert pending.status == "pending" and state.survival_tasks[0].due == 86400
    state = state.model_copy(update={"game_time": 86400})
    command = FinishSurvivalActivity(
        id="finish", actor_id="a", expected_revision=1, task_id="forage"
    )
    state, result = finish_foraging(
        state,
        command,
        forage_context(),
        rng=RecordedDice([3, 3, 3, 2, 2, 2]),
        system=True,
    )
    assert result.meals_produced == 3
    assert next(item for item in state.items if item.id == "forage:forage:rations").quantity == 3
    restored = ResourceState.model_validate_json(state.model_dump_json())
    replay, repeated = finish_foraging(
        restored,
        command,
        forage_context(),
        rng=RecordedDice([]),
        system=True,
    )
    assert replay == restored and repeated == result
    assert len([item for item in replay.items if item.id == "forage:forage:rations"]) == 1


def test_dedicated_foraging_gets_five_attempts_and_critical_poison_is_recorded() -> None:
    state, _ = begin_foraging(
        seed(),
        BeginForage(id="serious", actor_id="a", expected_revision=0, mode="dedicated"),
        ForagingContext(
            profile_id=PROFILE,
            plant_skill=12,
            animal_skill=None,
            animal_method=None,
            terrain_modifier=0,
            ration_definition_id="ration",
            supply_owner_id="a",
            party_ht=(("a", 10),),
        ),
        system=True,
    )
    state = state.model_copy(update={"game_time": 86400})
    state, result = finish_foraging(
        state,
        FinishSurvivalActivity(
            id="finish-serious", actor_id="a", expected_revision=1, task_id="serious"
        ),
        forage_context(),
        rng=RecordedDice(
            [6, 6, 5, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
        ),
        system=True,
    )
    assert len(result.forage_checks) == 5
    assert result.poisoned_actor_ids == ("a",) and result.hp_lost == 1
    assert next(pool for pool in state.pools if pool.id == "hp:a").current == 9


def test_activity_interruption_prevents_sleep_or_foraging_credit() -> None:
    state = start(seed())
    state, _ = begin_sleep(
        state,
        BeginSleep(id="sleep", actor_id="a", expected_revision=1, seconds=28800),
        context(),
        system=True,
    )
    state = state.model_copy(
        update={
            "game_time": 60,
            "survival_tasks": interrupt_survival_tasks(
                state.survival_tasks, frozenset({"a"}), 60
            ),
        }
    )
    state, result = finish_sleep(
        state,
        FinishSurvivalActivity(
            id="interrupted", actor_id="a", expected_revision=2, task_id="sleep"
        ),
        context(),
        system=True,
    )
    assert result.status == "interrupted" and result.fp_recovered == 0


def test_resource_clock_stops_at_survival_deadlines_and_activity_interrupts_sleep() -> None:
    from test_resources import engine as resource_engine
    from test_resources import seed as resource_seed

    status = SurvivalStatus(
        actor_id="a",
        started=0,
        next_meal_due=100,
        next_water_due=100,
        awake_since=0,
        next_sleep_due=100,
        water_day_started=0,
    )
    task = SurvivalTask(id="sleep", actor_id="a", kind="sleep", start=0, due=100)
    state = resource_seed().model_copy(
        update={"survival": (status,), "survival_tasks": (task,)}
    )
    reducer = resource_engine()
    with pytest.raises(ConflictError, match="survival deadline"):
        reducer.apply(
            state,
            Advance(id="too-far", actor_id="a", expected_revision=0, to=101),
            system=True,
        )
    interrupted = reducer.apply(
        state,
        Consume(
            id="activity",
            actor_id="a",
            expected_revision=0,
            item_id="arrows",
            quantity=1,
        ),
    )
    assert interrupted.survival_tasks[0].status == "interrupted"
