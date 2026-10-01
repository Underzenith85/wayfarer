"""B248 waking, B237 overlap, and B428/B440-441 continuing drug consequences."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_healing_spell_effects import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.healing import BINDINGS
from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.rules.types.survival import SurvivalStatus, SurvivalTask
from wayfarer.engine.rules.types.toxin import Intoxication, ToxinExposure, ToxinProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import fatigue_ready
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.health.sleep_state import asleep
from wayfarer.engine.simulation.health.toxins import (
    DrinkCommand,
    ToxinCommand,
    apply_drinking,
    apply_toxin,
)
from wayfarer.engine.simulation.magic.awaken import AwakenSubject, alerts, awaken, validate_subjects
from wayfarer.engine.simulation.magic.awaken_state import alert_until
from wayfarer.engine.simulation.magic.colleges import dispatch_college_spell
from wayfarer.engine.simulation.magic.spell_transitions import approved_context
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellResult, latest
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError


def area_state(tmp_path: Path) -> tuple[RulesContext, PlayState, SpellCommand]:
    runtime, state = fixture(tmp_path, "awaken")
    encounter = Encounter(
        id="fight",
        legacy_battlefield_id="room",
        turn_order=("a", "b"),
        participants=tuple(
            Combatant(
                actor_id=actor,
                initiative=5,
                position=GridPoint(x=x, y=1),
                reach=1,
                movement_allowance=5,
            )
            for actor, x in (("a", 1), ("b", 2))
        ),
    )
    state = state.model_copy(update={"encounters": (encounter,)})
    command = SpellCommand(
        id="start",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="awaken",
        cast_id="cast",
        channel_id="awaken",
        radius=2,
    )
    return runtime, state, command


def complete_cast(
    runtime: RulesContext, state: PlayState, command: SpellCommand, dice: list[int]
) -> tuple[PlayState, SpellResult]:
    return dispatch_college_spell(
        replace(runtime, rng=RecordedDice(dice)),
        state,
        command,
        BINDINGS,
        authorized_actor_id="a",
    )


def intoxicated(state: PlayState, *, fp: int = 10) -> PlayState:
    resources = state.resources.model_copy(
        update={
            "intoxications": (
                Intoxication(
                    actor_id="b",
                    window_started=0,
                    level="unconscious",
                    total_session_drinks=8,
                    sober_due=14400,
                    stopped_at=0,
                ),
            ),
            "pools": tuple(
                p.model_copy(update={"current": fp}) if p.id == "fp:b" else p
                for p in state.resources.pools
            ),
        }
    )
    return state.model_copy(
        update={
            "resources": resources,
            "actors": tuple(
                a.model_copy(update={"conditions": ("unconscious", "stunned", "restrained")})
                if a.actor_id == "b"
                else a
                for a in state.actors
            ),
        }
    )


@pytest.mark.parametrize("roll,wakes", [([2, 2, 2], True), ([3, 3, 3], False)])
def test_approved_drugged_waking_changes_consciousness_not_intoxication(
    tmp_path: Path, roll: list[int], wakes: bool
) -> None:
    runtime, initial, command = area_state(tmp_path)
    initial = intoxicated(initial)
    assert drug_unconscious(initial.resources, "b") and not fatigue_ready(initial, "b")
    with pytest.raises(ValidationError, match="unconscious"):
        guard(initial, "b", "spell")
    with pytest.raises(ValidationError, match="unconscious"):
        require_hazard_capacity(initial.resources, "b", "vision")
    changed, result = complete_cast(runtime, initial, command, [3, 3, 3] + roll)
    # B248: approved skill 13 - distance 1 = 12; roll 9 gives +3. HT10 +3 -6 =7.
    assert result.outcome == "active" and result.energy_spent == 2
    assert [trace.effective_target for trace in result.checks] == [12, 7]
    assert latest(changed.resources)["cast"].phase == "ended"
    patient = next(a for a in changed.actors if a.actor_id == "b")
    assert patient.conditions == (("restrained",) if wakes else ("unconscious", "restrained"))
    assert fatigue_ready(changed, "b") is wakes
    assert drug_unconscious(changed.resources, "b") is not wakes
    before, after = initial.resources.intoxications[0], changed.resources.intoxications[0]
    assert after == before
    # B428/B440: HT wake success gives neither sobriety nor immunity to DX/IQ penalties.
    assert [m.value for m in check_modifiers(changed.resources, "b", "dx")] == [-2]
    assert [m.value for m in check_modifiers(changed.resources, "b", "iq")] == [-2]
    assert not check_modifiers(changed.resources, "b", "ht")
    reloaded = PlayState.model_validate_json(changed.model_dump_json())
    assert reloaded == changed
    assert drug_unconscious(reloaded.resources, "b") is not wakes
    if wakes:
        guard(reloaded, "b", "spell")
        require_hazard_capacity(reloaded.resources, "b", "vision")


@pytest.mark.parametrize("fp,dead", [(0, False), (-1, False), (10, True)])
def test_ineligible_subject_preserves_every_condition_and_draws_no_wake_dice(
    tmp_path: Path, fp: int, dead: bool
) -> None:
    runtime, state, command = area_state(tmp_path)
    state = intoxicated(state, fp=fp)
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"injury": hp.injury.model_copy(update={"dead": dead})})
                        if p.id == hp.id
                        else p
                        for p in state.resources.pools
                    ),
                }
            )
        }
    )
    changed, result = complete_cast(runtime, state, command, [3, 3, 3])
    assert len(result.checks) == 1
    assert changed.actors[1].conditions == state.actors[1].conditions
    assert changed.resources.intoxications == state.resources.intoxications
    assert next(p for p in changed.resources.pools if p.id == "hp:b") == next(
        p for p in state.resources.pools if p.id == "hp:b"
    )
    assert not alerts(changed.resources)


@pytest.mark.parametrize("roll,wakes", [([3, 3, 3], True), ([5, 5, 5], False)])
def test_legacy_actor_only_unconsciousness_is_a_real_waking_subject(
    tmp_path: Path, roll: list[int], wakes: bool
) -> None:
    runtime, state, command = area_state(tmp_path)
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"conditions": ("unconscious", "stunned")})
                if a.actor_id == "b"
                else a
                for a in state.actors
            )
        }
    )
    changed, result = complete_cast(runtime, state, command, [3, 3, 3] + roll)
    assert (
        result.checks[-1].effective_target == 13
    )  # B248 HT10 + caster margin3, no recorded injury.
    assert changed.actors[1].conditions == (() if wakes else ("unconscious",))


def toxin(*, condition: str = "unconscious", resistible: bool = False) -> ToxinExposure:
    profile = ToxinProfile.model_validate(
        dict(
            id="sedative",
            vector="digestive",
            condition=condition,
            condition_seconds=1000,
            hp_add=1,
            interval=100,
            cycles=2,
            reference="B441",
            depressant=True,
            resistible=resistible,
        )
    )
    return ToxinExposure(
        id="dose",
        actor_id="b",
        profile=profile,
        identity_digest="0" * 64,
        started=0,
        due=100,
        remaining=1,
        ht=10,
        condition_until=1000,
        overdose_until=500,
    )


@pytest.mark.parametrize("resists,dice", [(False, []), (True, [1, 1, 1])])
def test_awaken_does_not_halt_toxin_and_fresh_failed_cycle_can_knock_out_again(
    tmp_path: Path, resists: bool, dice: list[int]
) -> None:
    _, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "toxins": (toxin(resistible=resists),),
            "pools": tuple(
                p.model_copy(update={"current": 10}) if p.id == "hp:b" else p
                for p in state.resources.pools
            ),
        }
    )
    awake, traces = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2]),
    )
    assert traces[0].effective_target == 6  # B248 HT10 +2 -6; overdose is not injury -3.
    assert awake.toxins[0] == resources.toxins[0]
    assert not drug_unconscious(awake, "b")
    due = ResourceState.model_validate_json(awake.model_dump_json()).model_copy(
        update={"game_time": 100}
    )
    command = ToxinCommand(
        id="next-cycle", actor_id="b", expected_revision=0, kind="resolve", exposure_id="dose"
    )
    changed, result = apply_toxin(due, command, rng=RecordedDice(dice), system=True)
    assert result.resisted is resists
    assert next(p.current for p in changed.pools if p.id == "hp:b") == (10 if resists else 9)
    assert drug_unconscious(changed, "b") is not resists
    assert apply_toxin(changed, command, rng=RecordedDice([]), system=True) == (changed, result)


def test_waking_overdose_keeps_paralysis_and_other_toxin_conditions(tmp_path: Path) -> None:
    _, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(update={"toxins": (toxin(condition="paralysis"),)})
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2]),
    )
    assert not drug_unconscious(awake, "b")
    with pytest.raises(ValidationError, match="Paralysis"):
        require_hazard_capacity(awake, "b", "move")
    assert awake.toxins[0].condition_until == 1000 and awake.toxins[0].overdose_until == 500


def test_awaken_does_not_cure_alcohol_coma_or_skip_sobering(tmp_path: Path) -> None:
    _, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "intoxications": (
                Intoxication(
                    actor_id="b",
                    window_started=0,
                    level="coma",
                    stopped_at=0,
                    sober_due=3600,
                    total_session_drinks=2,
                ),
            )
        }
    )
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2]),
    )
    assert awake.intoxications[0].level == "coma" and not drug_unconscious(awake, "b")
    with pytest.raises(ValidationError, match="medical care"):
        apply_drinking(
            awake.model_copy(update={"game_time": 3600}),
            DrinkCommand(id="sober", actor_id="b", expected_revision=0, kind="sober"),
            ht=10,
            st=10,
            rng=RecordedDice([]),
            system=True,
        )


def survival_status() -> SurvivalStatus:
    return SurvivalStatus(
        actor_id="b",
        started=0,
        next_meal_due=28800,
        next_water_due=86400,
        awake_since=0,
        next_sleep_due=57600,
        water_day_started=0,
        forced_asleep=True,
    )


@pytest.mark.parametrize("roll,wakes", [([3, 3, 3], True), ([5, 5, 5], False)])
def test_represented_sleep_wakes_without_granting_unearned_recovery(
    tmp_path: Path, roll: list[int], wakes: bool
) -> None:
    runtime, state, command = area_state(tmp_path)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "survival": (survival_status(),),
                    "survival_tasks": (
                        SurvivalTask(id="sleep", actor_id="b", kind="sleep", start=0, due=28800),
                    ),
                }
            )
        }
    )
    assert asleep(state.resources, "b")
    changed, result = complete_cast(runtime, state, command, [3, 3, 3] + roll)
    assert result.checks[-1].effective_target == 13
    assert asleep(changed.resources, "b") is not wakes
    assert changed.resources.survival_tasks[0].status == ("interrupted" if wakes else "pending")
    assert next(p for p in changed.resources.pools if p.id == "fp:b") == next(
        p for p in state.resources.pools if p.id == "fp:b"
    )
    assert changed.resources.survival[0].next_sleep_due == 57600
    ResourceState.model_validate_json(changed.resources.model_dump_json())


def test_sleep_rest_is_interrupted_but_ordinary_rest_is_not(tmp_path: Path) -> None:
    _, state = fixture(tmp_path, "awaken")
    sleep_rest = RecoveryTask(
        id="rest",
        actor_id="b",
        target_id="b",
        profile_id="gurps-basic-set-4e-2004",
        kind="rest",
        start=0,
        due=3600,
        sleep=True,
    )
    for sleeping in (True, False):
        resources = state.resources.model_copy(
            update={"recovery_tasks": (sleep_rest.model_copy(update={"sleep": sleeping}),)}
        )
        awake, traces = awaken(
            resources,
            (AwakenSubject(actor_id="b", ht=10),),
            margin=2,
            command_id="wake",
            rng=RecordedDice([2, 2, 2] if sleeping else []),
        )
        assert bool(traces) is sleeping
        assert awake.recovery_tasks[0].status == ("interrupted" if sleeping else "pending")


def test_unknown_hp_cause_and_due_sleep_remain_atomic_before_randomness(tmp_path: Path) -> None:
    _, state = fixture(tmp_path, "awaken")
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury
    unknown = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"injury": hp.injury.model_copy(update={"unconscious": True})})
                if p.id == hp.id
                else p
                for p in state.resources.pools
            )
        }
    )
    due_sleep = state.resources.model_copy(
        update={
            "game_time": 10,
            "survival_tasks": (
                SurvivalTask(id="sleep", actor_id="b", kind="sleep", start=0, due=10),
            ),
        }
    )
    for resources, error, match in (
        (unknown, ValidationError, "cause evidence"),
        (due_sleep, ConflictError, "due sleep"),
    ):
        frozen = resources.model_dump_json()
        with pytest.raises(error, match=match):
            awaken(
                resources,
                (AwakenSubject(actor_id="b", ht=10),),
                margin=2,
                command_id="wake",
                rng=RecordedDice([]),
            )
        assert resources.model_dump_json() == frozen


def test_unauthorized_and_stale_casts_reject_without_rng(tmp_path: Path) -> None:
    runtime, state, command = area_state(tmp_path)
    with pytest.raises(AuthorizationError):
        dispatch_college_spell(runtime, state, command, BINDINGS, authorized_actor_id="b")
    with pytest.raises(ConflictError, match="revision"):
        complete_cast(runtime, state, command.model_copy(update={"expected_revision": 3}), [])
    with pytest.raises(ValidationError, match="unique"):
        validate_subjects(state.resources, (AwakenSubject(actor_id="b", ht=10),) * 2)
    # Player command schema never accepts approved subjects or consciousness provenance.
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError):
        SpellCommand.model_validate({**command.model_dump(), "authored_unconscious": True})
    no_approval = state.model_copy(
        update={"actors": (state.actors[0], state.actors[1].model_copy(update={"approval": None}))}
    )
    with pytest.raises(ValidationError):
        approved_context(runtime, no_approval, command)


def test_overlapping_alertness_keeps_independent_nonrefundable_hour_costs(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                for p in state.resources.pools
            )
        }
    )
    subject = (AwakenSubject(actor_id="b", ht=10),)
    first, _ = awaken(resources, subject, margin=2, command_id="first", rng=RecordedDice([]))
    second, _ = awaken(
        first.model_copy(update={"game_time": 100}),
        subject,
        margin=2,
        command_id="second",
        rng=RecordedDice([]),
    )
    # B237: no doubled bonus; B248: each cast still costs 1 FP at its own hour end.
    assert alert_until(second, "b") == 3700
    assert [a.due for a in alerts(second)] == [3600, 3700]
    with pytest.raises(ConflictError, match="Awaken"):
        runtime.resources.apply(
            second,
            Advance(id="skip", actor_id="a", expected_revision=0, to=3700),
            system=True,
            rng=RecordedDice([]),
        )
    first_due = runtime.resources.apply(
        second,
        Advance(id="first-due", actor_id="a", expected_revision=0, to=3600),
        system=True,
        rng=RecordedDice([]),
    )
    assert next(p.current for p in first_due.pools if p.id == "fp:b") == 1
    assert alert_until(first_due, "b") == 3700
    last_command = Advance(
        id="second-due", actor_id="a", expected_revision=first_due.revision, to=3700
    )
    done = runtime.resources.apply(first_due, last_command, system=True, rng=RecordedDice([]))
    assert next(p.current for p in done.pools if p.id == "fp:b") == 0
    assert not alert_until(done, "b") and all(a.paid for a in alerts(done))
    assert (
        runtime.resources.apply(
            ResourceState.model_validate_json(done.model_dump_json()),
            last_command,
            system=True,
            rng=RecordedDice([]),
        )
        == done
    )


@pytest.mark.parametrize("kind", ["wake", "off-hand"])
@pytest.mark.parametrize(
    "level,penalty,succeeds", [("sober", 0, True), ("tipsy", -1, True), ("unconscious", -2, False)]
)
async def test_persisted_continuing_checks_use_intoxication_after_waking(
    tmp_path: Path, kind: str, level: str, penalty: int, succeeds: bool
) -> None:
    import json

    from support.runtime import seed_campaign
    from test_actions import campaign

    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.orchestration.physical_checks import (
        PhysicalCheck,
        PhysicalCheckCommand,
        PhysicalCheckService,
    )
    from wayfarer.orchestration.play import PlayService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    runtime, state = fixture(tmp_path, "awaken")
    intoxication = Intoxication.model_validate(dict(actor_id="b", window_started=0, level=level))
    resources = state.resources.model_copy(update={"intoxications": (intoxication,)})
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2] if level == "unconscious" else []),
    )
    state = state.model_copy(update={"resources": awake})
    engine = ActionEngine(runtime.reviewer, runtime.resources, runtime.rules)
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "checks.sqlite", 10), engine, rng=RecordedDice([3, 3, 3])
    )
    initial = campaign(engine)
    initial["id"], initial["play_json"] = state.campaign_id, state.model_dump_json()
    await seed_campaign(play.store, initial)
    spec = (
        PhysicalCheck(kind="wake") if kind == "wake" else PhysicalCheck(kind="off-hand", modifier=4)
    )
    service = PhysicalCheckService(play, lambda *_: spec)
    command = PhysicalCheckCommand(
        id="check", actor_id="b", expected_revision=0, trigger_id="check"
    )
    assert await service.execute(initial["id"], command, principal_id="gm") is succeeds
    saved = await play.store.read(initial["id"])
    changed = play._load(saved)
    event = next(e for e in changed.resources.events if e.id.startswith("physical-check:"))
    trace = json.loads(event.kind)
    assert trace["effective_target"] == 10 + penalty
    assert changed.resources.intoxications == awake.intoxications
    assert changed.actors == state.actors  # No permanent skill/statistic changes.
    assert saved == await play.store.replay(initial["id"])
    play.rng = RecordedDice([])
    assert await service.execute(initial["id"], command, principal_id="gm") is succeeds


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_awaken_transaction_retry_restart_authority_and_private_trace(
    tmp_path: Path, backend: str
) -> None:
    import asyncio
    import os

    from support.runtime import build_runtime, seed_campaign
    from test_actions import campaign

    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.combat.battlefield import Battlefield
    from wayfarer.engine.simulation.combat.profiles import CombatRules
    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.spells import SpellService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
    from wayfarer.persistence.postgres import AsyncPostgresStore

    runtime, state, command = area_state(tmp_path)
    rules = runtime.rules.model_copy(
        update={
            "combat": CombatRules(
                id="combat",
                version=1,
                battlefields=(Battlefield(id="room", location_id="room", width=5, height=5),),
            )
        }
    )
    engine = ActionEngine(runtime.reviewer, runtime.resources, rules)
    state = intoxicated(state).model_copy(
        update={
            "configuration_digest": engine.digest,
            "encounters": (
                state.encounters[0].model_copy(
                    update={
                        "participants": tuple(
                            p.model_copy(update={"initiative": 10})
                            for p in state.encounters[0].participants
                        )
                    }
                ),
            ),
        }
    )
    engine.validate(state)
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "awaken.sqlite", 10), engine, rng=RecordedDice([])
    )
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        play = PlayService(AsyncPostgresStore(url, 10), engine, rng=RecordedDice([]))
    initial = campaign(engine)
    initial["id"], initial["play_json"] = state.campaign_id, state.model_dump_json()
    await seed_campaign(play.store, initial)
    service = SpellService(play)
    with pytest.raises(AuthorizationError):
        await service.execute(initial["id"], command, principal_id="b")
    assert await play.store.read(initial["id"]) == initial
    play.rng = RecordedDice([3, 3, 3, 2, 2, 2])
    results = await asyncio.gather(
        *(service.execute(initial["id"], command, principal_id="a") for _ in range(3))
    )
    assert all(result == results[0] for result in results)
    saved = await play.store.read(initial["id"])
    after = play._load(saved)
    assert after.actors[1].conditions == ("restrained",)
    assert not drug_unconscious(after.resources, "b")
    assert after.resources.intoxications[0].level == "unconscious"
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 28
    assert saved == await play.store.replay(initial["id"])
    restarted = PlayService(play.store, engine, rng=RecordedDice([]))
    assert (
        await SpellService(restarted).execute(initial["id"], command, principal_id="a")
        == results[0]
    )
    assert await restarted.store.read(initial["id"]) == saved
    public = await build_runtime(restarted).events(initial["id"], principal_id="b")
    assert "effective_target" not in str(public) and "drug-wake:" not in str(public)
    with pytest.raises(ConflictError):
        await SpellService(restarted).execute(
            initial["id"], command.model_copy(update={"id": "stale"}), principal_id="a"
        )


def test_alert_suppresses_fatigue_drowsiness_until_the_last_interval_ends(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.health.survival import (
        SettleSurvival,
        SurvivalContext,
        settle_survival,
    )

    runtime, state = fixture(tmp_path, "awaken")
    fp = next(p for p in state.resources.pools if p.id == "fp:b")
    assert fp.fatigue
    resources = state.resources.model_copy(
        update={
            "survival": (
                survival_status().model_copy(
                    update={
                        "forced_asleep": False,
                        "drowsy_until": 7200,
                        "next_drowsiness_due": 100,
                    }
                ),
            ),
            "pools": tuple(
                p.model_copy(
                    update={"current": 2, "fatigue": fp.fatigue.model_copy(update={"sleep": 8})}
                )
                if p.id == fp.id
                else p
                for p in state.resources.pools
            ),
        }
    )
    assert [m.value for m in check_modifiers(resources, "b", "dx")] == [-2]
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([]),
    )
    assert not check_modifiers(awake, "b", "dx")
    during = awake.model_copy(update={"game_time": 100})
    context = SurvivalContext(profile_id="gurps-basic-set-4e-2004", ht=10, will=10, active=False)
    alert, result = settle_survival(
        during,
        SettleSurvival(id="drowsiness", actor_id="b", expected_revision=0),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.drowsiness_check is None and not alert.survival[0].forced_asleep
    assert alert.survival[0].next_drowsiness_due == 3600
    expired = runtime.resources.apply(
        alert,
        Advance(id="expire", actor_id="a", expected_revision=1, to=3600),
        rng=RecordedDice([]),
        system=True,
    )
    assert [m.value for m in check_modifiers(expired, "b", "dx")] == [-2]
    tired, result = settle_survival(
        expired,
        SettleSurvival(id="tired", actor_id="b", expected_revision=expired.revision),
        context,
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert result.drowsiness_check is not None and not result.drowsiness_check.outcome.succeeded
    assert tired.survival[0].forced_asleep
    assert next(p.current for p in tired.pools if p.id == "fp:b") == 1


def test_alert_deadline_accrues_rest_before_charge_without_stealing_the_task(
    tmp_path: Path,
) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                for p in state.resources.pools
            )
        }
    )
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([]),
    )
    rest = RecoveryTask(
        id="quiet-rest",
        actor_id="b",
        target_id="b",
        profile_id="gurps-basic-set-4e-2004",
        kind="rest",
        start=0,
        due=7200,
        ordinary_entitlement=8,
    )
    awake = awake.model_copy(update={"recovery_tasks": (rest,)})
    changed = runtime.resources.apply(
        awake,
        Advance(id="hour", actor_id="a", expected_revision=0, to=3600),
        system=True,
        rng=RecordedDice([]),
    )
    # B426 six FP earned; B248 then charges one. The clock's final accrual cannot refund it.
    assert next(p.current for p in changed.pools if p.id == "fp:b") == 7
    assert changed.recovery_tasks[0].status == "pending"
    assert changed.recovery_tasks[0].ordinary_granted == 6
    assert changed.recovery_tasks[0].fp_recovered_total == 6


def test_failed_cast_does_not_awaken_subject_or_charge_alert(tmp_path: Path) -> None:
    runtime, state, command = area_state(tmp_path)
    state = intoxicated(state, fp=2)
    changed, result = complete_cast(runtime, state, command, [5, 5, 5])
    assert result.outcome == "failed" and result.energy_spent == 1
    assert changed.actors[1] == state.actors[1]
    assert changed.resources.intoxications == state.resources.intoxications
    assert not alerts(changed.resources)


def test_same_deadline_recasts_pay_each_obligation_once_after_reload(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                for p in state.resources.pools
            )
        }
    )
    for cast_id in ("first", "second"):
        resources, traces = awaken(
            resources,
            (AwakenSubject(actor_id="b", ht=10),),
            margin=2,
            command_id=cast_id,
            rng=RecordedDice([]),
        )
        assert not traces  # B248 permits casting on an already-alert subject.
    assert alert_until(resources, "b") == 3600
    assert [a.due for a in alerts(resources)] == [3600, 3600]
    command = Advance(id="both-due", actor_id="a", expected_revision=0, to=3600)
    loaded = ResourceState.model_validate_json(resources.model_dump_json())
    changed = runtime.resources.apply(loaded, command, system=True, rng=RecordedDice([]))
    assert next(p.current for p in changed.pools if p.id == "fp:b") == 0
    assert all(a.paid for a in alerts(changed)) and not alert_until(changed, "b")
    reloaded = ResourceState.model_validate_json(changed.model_dump_json())
    assert runtime.resources.apply(reloaded, command, system=True, rng=RecordedDice([])) == reloaded
    later = runtime.resources.apply(
        reloaded,
        command.model_copy(
            update={
                "id": "after",
                "expected_revision": reloaded.revision,
                "to": 3601,
            }
        ),
        system=True,
        rng=RecordedDice([]),
    )
    assert later.pools == reloaded.pools


def test_new_toxin_effect_invalidates_only_its_own_private_wake_record(tmp_path: Path) -> None:
    _, state = fixture(tmp_path, "awaken")
    first = toxin()
    other = first.model_copy(update={"id": "other-dose"})
    resources = state.resources.model_copy(
        update={
            "toxins": (first, other),
            "pools": tuple(
                p.model_copy(update={"current": 10}) if p.id == "hp:b" else p
                for p in state.resources.pools
            ),
        }
    )
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2]),
    )
    assert awake.toxins == resources.toxins and not drug_unconscious(awake, "b")
    changed, _ = apply_toxin(
        awake.model_copy(update={"game_time": 100}),
        ToxinCommand(
            id="fresh-effect", actor_id="b", expected_revision=0, kind="resolve", exposure_id="dose"
        ),
        rng=RecordedDice([]),
        system=True,
    )
    assert drug_unconscious(changed, "b")
    assert changed.toxins[1] == other
    # Isolating the unchanged second exposure proves its earlier awakening remains effective.
    second_only = changed.model_copy(update={"toxins": (other,)})
    assert not drug_unconscious(
        ResourceState.model_validate_json(second_only.model_dump_json()), "b"
    )
    # A wholly new exposure has no wake record and cannot inherit another exposure's override.
    fresh = other.model_copy(update={"id": "fresh-dose"})
    assert drug_unconscious(second_only.model_copy(update={"toxins": (other, fresh)}), "b")


@pytest.mark.parametrize("level", ["unconscious", "coma"])
@pytest.mark.parametrize("purges", [False, True])
def test_further_alcohol_incapacitation_invalidates_waking_without_a_schema_field(
    tmp_path: Path,
    level: str,
    purges: bool,
) -> None:
    from pydantic import ValidationError as SchemaError

    _, state = fixture(tmp_path, "awaken")
    intoxication = Intoxication.model_validate(dict(actor_id="b", window_started=0, level=level))
    resources = state.resources.model_copy(update={"intoxications": (intoxication,)})
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="wake",
        rng=RecordedDice([2, 2, 2]),
    )
    command = DrinkCommand(
        id="more-alcohol", actor_id="b", expected_revision=0, kind="drink", drinks=3
    )
    changed, result = apply_drinking(
        awake,
        command,
        ht=10,
        st=10,
        rng=RecordedDice([6, 6, 6] + ([1, 1, 1] if purges else [5, 5, 5])),
        system=True,
    )
    assert result.level == (level if purges else "coma")
    assert drug_unconscious(changed, "b") is not purges
    assert apply_drinking(
        ResourceState.model_validate_json(changed.model_dump_json()),
        command,
        ht=10,
        st=10,
        rng=RecordedDice([]),
        system=True,
    ) == (changed, result)
    with pytest.raises(SchemaError):
        Intoxication.model_validate({**intoxication.model_dump(), "magically_awake": True})


def test_maximum_length_awaken_commands_keep_bounded_event_and_expiry_ids(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    resources = state.resources.model_copy(
        update={
            "toxins": (toxin().model_copy(update={"active": False, "remaining": 0}),),
            "pools": tuple(
                p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                for p in state.resources.pools
            ),
        }
    )
    awake, _ = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="x" * 200,
        rng=RecordedDice([2, 2, 2]),
    )
    assert not drug_unconscious(awake, "b") and len(alerts(awake)) == 1
    assert all(len(event.id) <= 200 for event in awake.events)
    expired = runtime.resources.apply(
        ResourceState.model_validate_json(awake.model_dump_json()),
        Advance(id="y" * 200, actor_id="a", expected_revision=0, to=3600),
        system=True,
        rng=RecordedDice([]),
    )
    assert next(p.current for p in expired.pools if p.id == "fp:b") == 1
    assert all(len(event.id) <= 200 for event in expired.events)
