"""Independent B53/B79/B95/B444 numerical expectations for issue #753."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import seed_campaign
from test_actions import campaign, world
from test_disease_aging import state as aging_state
from test_statistics import gurps_draft, profile_package
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.traits.physiology import PhysiologyTraits, physiology_traits
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environmental_hazards import radiation_spec
from wayfarer.engine.rules.traits.physiology import PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.physiology import package as physiology_package
from wayfarer.engine.rules.types.disease import YEAR_SECONDS, AgingRules
from wayfarer.engine.rules.types.hazard import HazardProtection, HazardSchedule
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.health.disease import (
    EnrollAging,
    ResolveAging,
    aging_schedules,
    apply_aging,
    due_health_effects,
)
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def traits(*purchases: Purchase) -> PhysiologyTraits:
    compiler = trait_compiler(
        "lifespan-radiation", PROFILE, physiology_package(), hooks=RUNTIME_HOOKS
    )
    build, _ = approved_build(compiler, *purchases)
    return physiology_traits(build, compiler.definitions)


@pytest.mark.parametrize(
    ("purchases", "scale", "age", "interval"),
    [
        ((), Fraction(1), 70, YEAR_SECONDS // 2),
        (
            (Purchase(definition_id="advantage:extended-lifespan", amount=2),),
            Fraction(4),
            280,
            2 * YEAR_SECONDS,
        ),
        (
            (Purchase(definition_id="disadvantage:short-lifespan", amount=2),),
            Fraction(1, 4),
            45,
            YEAR_SECONDS // 16,
        ),
        (
            (
                Purchase(definition_id="advantage:extended-lifespan", amount=1),
                Purchase(definition_id="disadvantage:short-lifespan", amount=2),
            ),
            Fraction(1, 2),
            35,
            YEAR_SECONDS // 4,
        ),
    ],
)
def test_purchased_lifespan_scales_thresholds_and_intervals(
    purchases: tuple[Purchase, ...], scale: Fraction, age: int, interval: int
) -> None:
    physiology = traits(*purchases)
    assert physiology.lifespan_multiplier() == scale
    threshold = int(50 * YEAR_SECONDS * scale)
    resources, enrolled = apply_aging(
        aging_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age"),
        rules=AgingRules(enabled=True, extended_lifespan_levels=2),
        age_seconds=threshold - 1,
        ht=10,
        physiology=physiology,
        rng=RecordedDice([]),
        system=True,
    )
    assert enrolled.due == 1
    with pytest.raises(ConflictError, match="not due"):
        apply_aging(
            resources,
            ResolveAging(id="early", actor_id="a", expected_revision=1, schedule_id="age"),
            physiology=physiology,
            rng=RecordedDice([]),
            system=True,
        )
    # Authored duplicate trait facts are replaced, never multiplied twice.
    assert aging_schedules(resources)[0].rules.extended_lifespan_levels == physiology.level(
        "advantage:extended-lifespan"
    )
    resources = resources.model_copy(update={"game_time": age * YEAR_SECONDS - threshold + 1})
    updated, result = apply_aging(
        resources,
        ResolveAging(id="roll", actor_id="a", expected_revision=1, schedule_id="age"),
        physiology=physiology,
        rng=RecordedDice([3, 3, 3] * 4),
        system=True,
    )
    # The first overdue check is still at the original fifty-scaled birthday.
    first_interval = int(YEAR_SECONDS * scale)
    assert result.due == 1 + first_interval
    # Subsequent enrollment directly at the band verifies the 70/90 thresholds.
    band, _ = apply_aging(
        aging_state(),
        EnrollAging(id="band", actor_id="a", expected_revision=0, schedule_id="band"),
        rules=AgingRules(enabled=True),
        age_seconds=age * YEAR_SECONDS,
        ht=10,
        physiology=physiology,
        rng=RecordedDice([]),
        system=True,
    )
    band, result = apply_aging(
        band,
        ResolveAging(id="band-roll", actor_id="a", expected_revision=1, schedule_id="band"),
        physiology=physiology,
        rng=RecordedDice([3, 3, 3] * 4),
        system=True,
    )
    assert result.due == interval
    assert aging_schedules(updated)[0].last_check_at == 1
    assert len(aging_schedules(band)[0].checks) == 4


def test_live_lifespan_removal_retimes_without_double_application() -> None:
    long = traits(Purchase(definition_id="advantage:extended-lifespan"))
    resources, _ = apply_aging(
        aging_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age"),
        rules=AgingRules(enabled=True),
        age_seconds=50 * YEAR_SECONDS,
        ht=10,
        physiology=long,
        rng=RecordedDice([]),
        system=True,
    )
    assert aging_schedules(resources)[0].due == 50 * YEAR_SECONDS
    updated, result = apply_aging(
        resources,
        ResolveAging(id="removed", actor_id="a", expected_revision=1, schedule_id="age"),
        physiology=traits(),
        rng=RecordedDice([3, 3, 3] * 4),
        system=True,
    )
    assert result.due == YEAR_SECONDS and len(result.checks) == 4
    assert aging_schedules(updated)[0].rules.extended_lifespan_levels == 0
    transformed, result = apply_aging(
        updated,
        ResolveAging(id="transformed", actor_id="a", expected_revision=2, schedule_id="age"),
        physiology=long,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.due == 2 * YEAR_SECONDS and not result.checks
    assert aging_schedules(transformed)[0].rules.extended_lifespan_levels == 1


def test_unaging_suspends_deadlines_and_removal_resumes_with_exact_retry() -> None:
    immortal = traits(Purchase(definition_id="advantage:unaging"))
    assert immortal.lifespan_multiplier() is None
    resources, result = apply_aging(
        aging_state(),
        EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age"),
        rules=AgingRules(enabled=True),
        age_seconds=90 * YEAR_SECONDS,
        ht=10,
        physiology=immortal,
        rng=RecordedDice([]),
        system=True,
    )
    assert not result.active and result.due is None
    assert not due_health_effects(resources, frozenset({"a"}), YEAR_SECONDS)
    resources = resources.model_copy(update={"game_time": 20 * YEAR_SECONDS})
    command = ResolveAging(id="resume", actor_id="a", expected_revision=1, schedule_id="age")
    updated, result = apply_aging(
        resources, command, physiology=traits(), rng=RecordedDice([3, 3, 3] * 4), system=True
    )
    assert result.active and result.due == 20 * YEAR_SECONDS + YEAR_SECONDS // 4
    assert len(result.checks) == 4
    assert aging_schedules(updated)[0].started == 20 * YEAR_SECONDS
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_aging(
        restarted, command, physiology=immortal, rng=RecordedDice([]), system=True
    ) == (restarted, result)
    with pytest.raises(ConflictError, match="revision"):
        apply_aging(
            restarted,
            command.model_copy(update={"id": "stale"}),
            physiology=traits(),
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ValidationError, match="authoritative"):
        apply_aging(restarted, command, physiology=traits(), rng=RecordedDice([]))


@pytest.mark.parametrize(("divisor", "dose"), [(2, 100), (5, 40), (20, 10), (1000, 0)])
def test_radiation_divisor_applies_once_to_new_dose_and_removed_traits(
    divisor: int, dose: int
) -> None:
    protected = traits(
        Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=divisor))
    )
    spec = radiation_spec(
        id="reactor",
        scene_id="core",
        rads=400,
        interval=3600,
        cycles=2,
        protection=HazardProtection(radiation_pf=2),
    )
    schedule = HazardSchedule(
        id="exposure",
        actor_id="a",
        spec=spec,
        started=0,
        due=0,
        remaining=2,
        ht=10,
        will=10,
        swimming=10,
        full_hp=10,
    )
    resources = ResourceState(
        hazards=(schedule,),
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        ),
    )
    command = HazardCommand(
        id="tick", actor_id="a", expected_revision=0, kind="resolve", hazard_id="reactor"
    )
    updated, result = apply_hazard(
        resources, command, schedule, physiology=protected, rng=RecordedDice([2, 2, 2]), system=True
    )
    assert result.radiation_dose == dose
    assert updated.hazards[0].radiation_original == dose
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_hazard(
        restarted, command, schedule, physiology=traits(), rng=RecordedDice([]), system=True
    ) == (restarted, result)
    current = updated.hazards[0]
    updated = updated.model_copy(update={"game_time": current.due})
    removed, result = apply_hazard(
        updated,
        command.model_copy(update={"id": "removed", "expected_revision": 1}),
        current,
        physiology=traits(),
        rng=RecordedDice([]),
        system=True,
    )
    assert result.radiation_dose == dose + 200
    assert removed.hazards[0].radiation_original == dose + 200


async def radiation_campaign(tmp_path: Path, tolerant: bool) -> tuple[str, PlayService]:
    compiler = trait_compiler(
        "lifespan-radiation", PROFILE, physiology_package(), hooks=RUNTIME_HOOKS
    )
    base = profile_package(PROFILE)
    combined = replace(
        base,
        id="package:test-lifespan-radiation",
        definitions=base.definitions + physiology_package().definitions,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(world(), RulesCatalog((combined,)), compiler.rules, compiler.policy, ()),
        ActionRules(id="radiation-test", version=1, fatigue_cost=0, maximum_wait=10000),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "radiation.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    purchases = (
        (Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=20)),)
        if tolerant
        else ()
    )
    initial["play_json"] = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=gurps_draft(*purchases)),
                aware_of=("alley",),
            ),
        ),
    ).model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play


@pytest.mark.parametrize(("tolerant", "dose", "target"), [(False, 100, 7), (True, 5, 10)])
async def test_hazard_service_activates_real_purchase_and_preserves_cas_restart(
    tmp_path: Path, tolerant: bool, dose: int, target: int
) -> None:
    cid, play = await radiation_campaign(tmp_path, tolerant)
    before = play._load(await play.store.read(cid))
    scene = next(e.location_id for e in before.world.entities if e.id == "a")
    assert scene is not None
    spec = radiation_spec(
        id="reactor",
        scene_id=scene,
        rads=200,
        interval=3600,
        cycles=1,
        protection=HazardProtection(radiation_pf=2),
    )
    service = HazardService(play, lambda *_: HazardContext(spec))
    await service.execute(
        cid,
        HazardCommand(
            id="enter", actor_id="a", expected_revision=0, kind="enter", hazard_id=spec.id
        ),
        principal_id="a",
    )
    command = HazardCommand(
        id="tick", actor_id="a", expected_revision=1, kind="resolve", hazard_id=spec.id
    )
    unchanged = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await service.execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == unchanged
    play.rng = RecordedDice([2, 2, 2])
    result = await service.execute(cid, command, principal_id="a")
    assert result.radiation_dose == dose
    assert result.check is not None and result.check.effective_target == target
    assert result.conditions == (() if tolerant else ("radiation-b",))
    after = play._load(await play.store.read(cid))
    assert after.resources.hazards[0].radiation_dose == dose
    restart = HazardService(
        PlayService(play.store, play.engine, rng=RecordedDice([])),
        lambda *_: (_ for _ in ()).throw(AssertionError("retry must not rerun resolver")),
    )
    assert await restart.execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_authored_unaging_still_requires_projection_and_context_is_preserved() -> None:
    command = EnrollAging(id="enroll", actor_id="a", expected_revision=0, schedule_id="age")
    with pytest.raises(ValidationError, match="must be enabled"):
        apply_aging(
            aging_state(),
            command,
            rules=AgingRules(enabled=True, unaging=True),
            age_seconds=50 * YEAR_SECONDS,
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
    resources, _ = apply_aging(
        aging_state(),
        command,
        rules=AgingRules(enabled=True, technology_level=8, longevity=True),
        age_seconds=50 * YEAR_SECONDS,
        ht=10,
        physiology=traits(),
        rng=RecordedDice([]),
        system=True,
    )
    projected = aging_schedules(resources)[0].rules
    assert projected.technology_level == 8 and projected.longevity
