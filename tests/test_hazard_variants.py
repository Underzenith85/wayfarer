"""B349-355/B430-443 expected results, authoritative saves and replay for #154."""

from decimal import Decimal
from pathlib import Path

import pytest
from test_medical_service import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environment import ambient_spec, poison_spec
from wayfarer.engine.rules.physical import contagion_modifier, falling_damage, falling_injury
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareCommand, HazardCareService
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def seed(play: PlayService, cid: str, state: PlayState, path: Path) -> None:
    campaign = await play.store.read(cid)
    campaign["play_json"] = state.model_dump_json()
    play.store = AsyncSQLiteStore(path, 10)
    await play.store.insert(campaign)


def schedule(spec: HazardSpec, *, stage: str = "cycles") -> HazardSchedule:
    return HazardSchedule.model_validate(
        dict(
            id="exposure",
            actor_id="a",
            spec=spec,
            started=0,
            due=spec.delay,
            remaining=spec.cycles,
            ht=10,
            will=10,
            swimming=10,
            stage=stage,
        )
    )


async def test_long_climb_checks_at_start_and_five_minutes_and_replays(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    route = PhysicalRoute(
        id="wall", scene_id="dock", kind="climb", surface="wall", distance=Decimal(40)
    )
    service = PhysicalService(play, lambda *_: route)
    play.rng = RecordedDice([1, 1, 1, 1, 1, 1])
    first = PhysicalCommand(
        id="climb-1", actor_id="a", expected_revision=0, kind="climb", route_id="wall"
    )
    result = await service.execute(cid, first, authenticated_actor_id="a")
    assert result.progress == "20" and not result.completed and result.seconds == 300
    result = await service.execute(
        cid,
        first.model_copy(update={"id": "climb-2", "expected_revision": 1}),
        authenticated_actor_id="a",
    )
    assert result.progress == "40" and result.completed and result.elapsed == 600
    play.rng = RecordedDice([])
    assert (await service.execute(cid, first, authenticated_actor_id="a")).progress == "20"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_failed_initial_climb_does_not_fall_from_top(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    play.rng = RecordedDice([5, 5, 5])
    route = PhysicalRoute(
        id="wall", scene_id="dock", kind="climb", surface="wall", distance=Decimal(40)
    )
    result = await PhysicalService(play, lambda *_: route).execute(
        cid,
        PhysicalCommand(
            id="fall", actor_id="a", expected_revision=0, kind="climb", route_id="wall"
        ),
        authenticated_actor_id="a",
    )
    assert not result.succeeded and result.injury == 0 and result.elapsed == 0


async def test_hiking_roll_is_daily_across_routes(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    play.rng = RecordedDice([1, 1, 1])
    route = PhysicalRoute(
        id="road", scene_id="dock", kind="hike", seconds=3600, distance=Decimal(100)
    )
    service = PhysicalService(play, lambda *_: route)
    for rev in range(2):
        result = await service.execute(
            cid,
            PhysicalCommand(
                id=f"hike-{rev}", actor_id="a", expected_revision=rev, kind="hike", route_id="road"
            ),
            authenticated_actor_id="a",
        )
        assert len(result.checks) == (1 if rev == 0 else 0)
    assert Decimal(result.progress) == 15 and result.fp_lost == 1
    state = play._load(await play.store.read(cid))
    assert len([e for e in state.resources.events if e.id.startswith("hiking-day:")]) == 1


async def test_swimming_minute_fatigue_survives_split_commands(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    route = PhysicalRoute(
        id="water", scene_id="dock", kind="swim", seconds=30, distance=Decimal(100)
    )
    service = PhysicalService(play, lambda *_: route)
    play.rng = RecordedDice([1, 1, 1, 5, 5, 5])
    for rev in range(2):
        result = await service.execute(
            cid,
            PhysicalCommand(
                id=f"swim-{rev}", actor_id="a", expected_revision=rev, kind="swim", route_id="water"
            ),
            authenticated_actor_id="a",
        )
    assert result.elapsed == 60 and result.progress == "60" and result.fp_lost == 1
    assert len(result.checks) == 1 and result.checks[0].effective_target == 10


def test_nonhuman_gravity_pressure_and_armor_expected_results() -> None:
    # B431's 17-yard example gives 4d for HP10 on hard ground.
    assert falling_damage(10, Decimal(17)) == (4, 0)
    assert falling_damage(20, Decimal(17)) == (8, 0)
    assert falling_damage(10, Decimal(17), gravity=Decimal("0.25")) == (2, 0)
    assert falling_damage(10, Decimal(10000), pressure=Decimal(4)) == (6, 0)
    assert falling_damage(10, Decimal(10000), terminal_velocity=100) == (20, 0)
    assert falling_damage(10, Decimal(10000), pressure=Decimal(0)) == (93, 0)
    assert falling_injury(12, 20) == 2
    assert falling_injury(12, 8) == 4
    assert falling_injury(12, 20, 10) == 0


def test_temperature_tolerance_survival_parameters() -> None:
    cold = HazardSpec(id="cold", scene_id="dock", kind="cold", interval=1800, reference="B430")
    selected = ambient_spec(cold, temperature=-20, ht=10, wind=30, clothing="arctic", wet=True)
    assert (selected.interval, selected.delay, selected.resistance_modifier) == (600, 600, -2)
    with pytest.raises(ValidationError, match="comfort"):
        ambient_spec(cold, temperature=20, ht=10, tolerance=2, cold_extension=20)
    heat = HazardSpec(id="heat", scene_id="dock", kind="heat", interval=1800, reference="B434")
    assert ambient_spec(heat, temperature=110, ht=10).resistance_modifier == -2
    with pytest.raises(ValidationError, match="threshold"):
        ambient_spec(heat, temperature=100, ht=10, tolerance=2)
    with pytest.raises(ValidationError, match="allocation"):
        ambient_spec(cold, temperature=-20, ht=10, cold_extension=20)


@pytest.mark.parametrize(
    ("variant", "delay", "interval", "cycles", "dice", "modifier"),
    [
        ("arsenic", 3600, 3600, 8, 1, -2),
        ("cobra-venom", 60, 3600, 6, 2, -3),
        ("cyanide-blood", 0, 1, 1, 4, 0),
        ("cyanide-digestive", 900, 1, 1, 4, 0),
        ("mustard-contact", 0, 28800, 24, 0, -4),
        ("mustard-respiratory", 7200, 3600, 6, 1, -1),
    ],
)
def test_named_poison_profiles(
    variant: str, delay: int, interval: int, cycles: int, dice: int, modifier: int
) -> None:
    spec = poison_spec(variant, id="toxin", scene_id="dock")
    assert (spec.delay, spec.interval, spec.cycles, spec.damage_dice, spec.resistance_modifier) == (
        delay,
        interval,
        cycles,
        dice,
        modifier,
    )


async def test_tear_gas_condition_without_toxic_injury(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    spec = poison_spec("tear-gas-respiratory", id="gas", scene_id="dock", exposure_seconds=10)
    hazard = schedule(spec)
    state = state.model_copy(update={"hazards": (hazard,)})
    state, result = apply_hazard(
        state,
        HazardCommand(id="gas", actor_id="a", expected_revision=0, kind="resolve", hazard_id="gas"),
        hazard,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert result.hp_lost == 0 and state.hazards[0].affliction_until == 250
    assert sum(m.value for m in check_modifiers(state, "a", "dx")) == -3
    with pytest.raises(ValidationError, match="Stealth"):
        require_hazard_capacity(state, "a", "stealth")
    assert check_modifiers(state.model_copy(update={"game_time": 250}), "a", "dx") == ()


async def test_disease_transmission_incubation_and_cycle_are_separate(tmp_path: Path) -> None:
    assert contagion_modifier(("dwelling", "intimate", "touch")) == -3
    cid, play, _ = await setup(tmp_path)
    spec = HazardSpec(
        id="flu",
        scene_id="dock",
        kind="disease",
        delay=86400,
        interval=43200,
        cycles=6,
        resistance_modifier=-2,
        reference="B443",
    )
    state = play._load(await play.store.read(cid)).resources
    hazard = schedule(spec, stage="exposure").model_copy(update={"resistance_bonus": -3})
    state = state.model_copy(update={"game_time": 86400, "hazards": (hazard,)})
    state, result = apply_hazard(
        state,
        HazardCommand(
            id="catch", actor_id="a", expected_revision=0, kind="resolve", hazard_id="flu"
        ),
        hazard,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.check is not None and result.check.effective_target == 5
    assert result.hp_lost == 0 and result.due == 172800 and state.hazards[0].remaining == 6
    hazard = state.hazards[0]
    state = state.model_copy(update={"game_time": result.due})
    state, result = apply_hazard(
        state,
        HazardCommand(
            id="sick", actor_id="a", expected_revision=1, kind="resolve", hazard_id="flu"
        ),
        hazard,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.check is not None and result.check.effective_target == 8
    assert result.hp_lost == 1 and state.illnesses[0].hp_debt == 1


async def test_diagnosis_durable_and_failed_attempt_cannot_reroll(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    spec = HazardSpec(
        id="flu", scene_id="dock", kind="disease", interval=86400, cycles=6, reference="B443"
    )
    hazard = schedule(spec).model_copy(update={"symptoms": 1, "due": 86400})
    before = play._load(await play.store.read(cid))
    await seed(
        play,
        cid,
        before.model_copy(
            update={"resources": before.resources.model_copy(update={"hazards": (hazard,)})}
        ),
        tmp_path / "diagnosis.sqlite",
    )
    service = HazardCareService(play, lambda *_: HazardCare("diagnosis", "a", "exposure"))
    play.rng = RecordedDice([1, 1, 1])
    cmd = HazardCareCommand(id="diagnose", actor_id="a", expected_revision=0, treatment_id="exam")
    result = await service.execute(cid, cmd, authenticated_actor_id="a")
    assert result.succeeded
    play.rng = RecordedDice([])
    assert await service.execute(cid, cmd, authenticated_actor_id="a") == result
    with pytest.raises(ConflictError, match="already attempted"):
        await service.execute(
            cid,
            cmd.model_copy(update={"id": "retry", "expected_revision": 1}),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_rescued_drowning_retains_deadline_and_uses_medical_task(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid)).resources
    spec = HazardSpec(
        id="water", scene_id="dock", kind="drowning", interval=5, cycles=1000, reference="B436"
    )
    hazard = schedule(spec, stage="rescued").model_copy(update={"due": 60, "no_air_since": 0})
    state = state.model_copy(update={"hazards": (hazard,)})
    context = CareContext("gurps-basic-set-4e-2004", 10, skill=12)
    state, result = apply_recovery(
        state,
        BeginRecovery(
            id="cpr", actor_id="b", expected_revision=0, kind="resuscitate", target_id="a"
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "pending" and state.recovery_tasks[0].due == 60
    state = state.model_copy(update={"game_time": 60})
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="b", expected_revision=1, task_id="cpr"),
        context,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.resuscitated and not state.hazards[0].active
    assert next(p.current for p in state.pools if p.id == "hp:a") == 5


async def travel_setup(tmp_path: Path, *, group: bool = False) -> tuple[str, PlayService]:
    from test_actions import campaign, world
    from test_statistics import gurps_draft

    from wayfarer.engine.character.power import CharacterProposal
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.campaign.scenes import Scene, SceneExit, SceneRules
    from wayfarer.engine.simulation.resources import Owner, ResourceState
    from wayfarer.orchestration.play import PlayService

    _, original, _ = await setup(tmp_path)
    rules = SceneRules(
        id="travel",
        version=1,
        scenes=(
            Scene(
                id="dock-scene",
                version=1,
                location_id="dock",
                title="Dock",
                exits=(SceneExit(id="path", destination_id="alley-scene"),),
            ),
            Scene(id="alley-scene", version=1, location_id="alley", title="Alley"),
        ),
    )
    engine = ActionEngine(
        original.engine.reviewer,
        original.engine.resources,
        original.engine.rules.model_copy(update={"scenes": rules}),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "travel.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    members = ("a", "b") if group else ("a",)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=tuple(Owner(actor_id=m, capacity=100) for m in members)),
        tuple(
            ActorSetup(
                actor_id=m, proposal=CharacterProposal(draft=gurps_draft()), aware_of=("alley",)
            )
            for m in members
        ),
    )
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    return initial["id"], play


async def test_completed_climb_uses_scene_exit_without_extra_time(tmp_path: Path) -> None:
    cid, play = await travel_setup(tmp_path)
    play.rng = RecordedDice([1, 1, 1, 1, 1, 1])
    route = PhysicalRoute(
        id="wall",
        scene_id="dock",
        kind="climb",
        surface="wall",
        distance=Decimal(40),
        exit_id="path",
        destination_id="alley-scene",
    )
    service = PhysicalService(play, lambda *_: route)
    for rev in range(2):
        result = await service.execute(
            cid,
            PhysicalCommand(
                id=f"climb-{rev}",
                actor_id="a",
                expected_revision=rev,
                kind="climb",
                route_id="wall",
            ),
            authenticated_actor_id="a",
        )
    assert result.completed
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 600
    assert state.actor_scenes[0].scene_id == "alley-scene"
    assert (
        state.party.groups[0].scene_id == "alley-scene"
        and state.party.groups[0].ready_through == 600
    )
    assert next(e.location_id for e in state.world.entities if e.id == "a") == "alley"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_full_day_group_hike_moves_together_and_preserves_clocks(tmp_path: Path) -> None:
    cid, play = await travel_setup(tmp_path, group=True)
    play.rng = RecordedDice([1, 1, 1, 1, 1, 1])
    route = PhysicalRoute(
        id="road",
        scene_id="dock",
        kind="hike",
        distance=Decimal(60),
        seconds=86400,
        group_hike=True,
        exit_id="path",
        destination_id="alley-scene",
    )
    result = await PhysicalService(play, lambda *_: route).execute(
        cid,
        PhysicalCommand(id="hike", actor_id="a", expected_revision=0, kind="hike", route_id="road"),
        authenticated_actor_id="a",
    )
    assert result.completed and Decimal(result.progress) == 60 and result.fp_lost == 0
    assert len(result.checks) == 2
    state = play._load(await play.store.read(cid))
    assert {c.scene_id for c in state.actor_scenes} == {"alley-scene"}
    assert state.resources.game_time == state.party.groups[0].ready_through == 86400
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_lifesaving_check_and_durable_rescue(tmp_path: Path) -> None:
    cid, play = await travel_setup(tmp_path, group=True)
    before = play._load(await play.store.read(cid))
    spec = HazardSpec(
        id="water", scene_id="dock", kind="drowning", interval=5, cycles=1000, reference="B354"
    )
    hazard = schedule(spec, stage="struggling").model_copy(update={"actor_id": "b", "due": 5})
    await seed(
        play,
        cid,
        before.model_copy(
            update={"resources": before.resources.model_copy(update={"hazards": (hazard,)})}
        ),
        tmp_path / "rescue.sqlite",
    )
    play.rng = RecordedDice([1, 1, 1])
    service = HazardCareService(
        play, lambda *_: HazardCare("lifesaving", "b", "exposure", safe_landing=True)
    )
    command = HazardCareCommand(id="save", actor_id="a", expected_revision=0, treatment_id="bank")
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert result.succeeded and result.check is not None and result.check.effective_target == 1
    assert play._load(await play.store.read(cid)).resources.hazards[0].stage == "rescued"
    play.rng = RecordedDice([])
    assert await service.execute(cid, command, authenticated_actor_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_temperature_tolerance_compiles_as_a_purchased_trait() -> None:
    from test_mundane_traits import runtime_compiler
    from test_statistics import gurps_draft

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.character.traits.physical import physical_traits

    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(Purchase(definition_id="trait:temperature-tolerance", amount=2))
    )
    assert result.legal and result.build is not None
    assert result.build.spent == 2
    assert physical_traits(result.build, compiler.definitions).temperature_tolerance == 2


@pytest.mark.parametrize(
    ("bacterial", "resistant", "bonus"), [(True, False, 3), (True, True, 0), (False, False, 0)]
)
async def test_antibiotics_consume_one_bound_dose_and_never_stack(
    tmp_path: Path,
    bacterial: bool,
    resistant: bool,
    bonus: int,
) -> None:
    from dataclasses import replace

    from test_actions import campaign, world
    from test_statistics import gurps_draft, profile_compiler, profile_package

    from wayfarer.engine.character.compiler import CharacterCompiler
    from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.engine.rules.catalog import (
        DefinitionKind,
        ImplementationStatus,
        RuleDefinition,
        RulesCatalog,
    )
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
    from wayfarer.engine.simulation.resources import (
        EquipmentSpec,
        Item,
        Owner,
        ResourceEngine,
        ResourceState,
    )

    profile = "gurps-basic-set-4e-2004"
    base = profile_package(profile)
    drug = RuleDefinition(
        "antibiotic",
        DefinitionKind.EQUIPMENT,
        "Antibiotic dose",
        base.sources[0].id,
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    package = profile_package(profile, drug)
    compiler = profile_compiler(profile, package=package)
    policy = replace(compiler.policy, allowed_equipment=frozenset({"antibiotic"}))
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(catalog, compiler.rules, policy, statistics_profile=profile)
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="care", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(),
            catalog,
            compiler.rules,
            policy,
            (EquipmentSpec(definition_id="antibiotic", unit_weight=0),),
        ),
        ActionRules(id="care", version=1),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "drug.sqlite", 10), engine, rng=RecordedDice([]))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=10),),
            items=(Item(id="dose", definition_id="antibiotic", owner_id="a", quantity=2),),
        ),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=gurps_draft())),),
    )
    spec = HazardSpec(
        id="illness",
        scene_id="dock",
        kind="disease",
        interval=86400,
        cycles=5,
        reference="B443",
        bacterial=bacterial,
        drug_resistant=resistant,
    )
    hazard = schedule(spec).model_copy(update={"due": 86400, "diagnosed_by": ("a",), "symptoms": 1})
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"hazards": (hazard,)})}
    )
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    cid = initial["id"]
    service = HazardCareService(
        play,
        lambda *_: HazardCare("antibiotics", "a", "exposure", item_id="dose", technology_level=6),
    )
    command = HazardCareCommand(
        id="medicine", actor_id="a", expected_revision=0, treatment_id="dose"
    )
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert result.treatment_bonus == bonus and result.succeeded == (bonus > 0)
    after = play._load(await play.store.read(cid))
    assert (
        after.resources.items[0].quantity == 1
        and after.resources.hazards[0].treatment_bonus == bonus
    )
    assert await service.execute(cid, command, authenticated_actor_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
    if bonus:
        with pytest.raises(ConflictError, match="already active"):
            await service.execute(
                cid,
                command.model_copy(update={"id": "double", "expected_revision": 1}),
                authenticated_actor_id="a",
            )


async def test_temperature_and_survival_are_consumed_by_hazard_service(tmp_path: Path) -> None:
    from dataclasses import replace

    from test_actions import campaign, world
    from test_mundane_traits import combined_package
    from test_statistics import gurps_draft, profile_compiler

    from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
    from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.engine.rules.catalog import RulesCatalog
    from wayfarer.engine.rules.skills.gurps_skills import definitions
    from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
    from wayfarer.engine.simulation.resources import Owner, ResourceEngine, ResourceState

    profile = "gurps-basic-set-4e-2004"
    package = combined_package()
    skill = next(d for d in definitions(profile) if d.id == "skill:survival-woodlands")
    swimming = next(d for d in definitions(profile) if d.id == "skill:swimming")
    package = replace(package, definitions=package.definitions + (skill, swimming))
    compiler = profile_compiler(profile, package=package)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        compiler.rules,
        compiler.policy,
        statistics_profile=profile,
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="weather", version=1), frozenset({"gm"})),
        ResourceEngine(world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        ActionRules(id="weather", version=1, maximum_wait=10000),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "weather.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    # Per+5 Survival (Woodlands) defaults to Desert at -3: HT-based target 12.
    draft = gurps_draft(
        Purchase(definition_id="trait:temperature-tolerance", amount=2),
        Purchase(definition_id="skill:survival-woodlands", amount=20),
        Purchase(definition_id="skill:swimming", amount=16),
    )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),),
    )
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    spec = HazardSpec(
        id="sun", scene_id="dock", kind="heat", interval=1800, cycles=2, reference="B434"
    )
    service = HazardService(play, lambda *_: HazardContext(spec, temperature_f=120))
    cid = initial["id"]
    await service.execute(
        cid,
        HazardCommand(id="enter", actor_id="a", expected_revision=0, kind="enter", hazard_id="sun"),
        authenticated_actor_id="a",
    )
    hazard = play._load(await play.store.read(cid)).resources.hazards[0]
    assert hazard.survival == 12 and hazard.spec.resistance_modifier == -1
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=1800),
        authenticated_actor_id="a",
    )
    play.rng = RecordedDice([3, 4, 4])
    result = await service.execute(
        cid,
        HazardCommand(
            id="check", actor_id="a", expected_revision=2, kind="resolve", hazard_id="sun"
        ),
        authenticated_actor_id="a",
    )
    assert result.check is not None and result.check.effective_target == 11 and result.fp_lost == 0
    play.rng = RecordedDice([3, 3, 3, 5, 5, 4])
    route = PhysicalRoute(
        id="swim", scene_id="dock", kind="swim", seconds=60, distance=Decimal(100)
    )
    swim = await PhysicalService(play, lambda *_: route).execute(
        cid,
        PhysicalCommand(id="swim", actor_id="a", expected_revision=3, kind="swim", route_id="swim"),
        authenticated_actor_id="a",
    )
    assert swim.checks[-1].effective_target == 15 and swim.fp_lost == 0


async def test_rigid_armor_fall_applies_blunt_trauma_through_inventory(tmp_path: Path) -> None:
    from test_gurps_melee import setup as melee_setup

    from wayfarer.engine.simulation.equipment.catalog import LITE_SOURCE, Armor, EquipmentProfile
    from wayfarer.engine.simulation.resources import Item

    armor = EquipmentProfile(
        definition_id="equipment:fall-armor",
        provenance=LITE_SOURCE,
        weight_millipounds=0,
        price=1,
        technology_level=1,
        slot="torso",
        armor=Armor(dr=20, flexible=False, locations=("torso",)),
    )
    cid, play = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_equipment=(armor,),
        extra_items=(
            Item(id="armor", definition_id=armor.definition_id, owner_id="a", equipped=True),
        ),
    )
    before = play._load(await play.store.read(cid))
    await seed(
        play,
        cid,
        before.model_copy(update={"encounters": (), "last_combat_result": None}),
        tmp_path / "fall.sqlite",
    )
    play.rng = RecordedDice([5, 5])
    route = PhysicalRoute(id="ledge", scene_id="dock", kind="fall", distance=Decimal(5))
    command = PhysicalCommand(
        id="fall", actor_id="a", expected_revision=before.revision, kind="fall", route_id="ledge"
    )
    result = await PhysicalService(play, lambda *_: route).execute(
        cid, command, authenticated_actor_id="a"
    )
    assert result.injury == 2 and result.completed
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 8
    assert await play.store.read(cid) == await play.store.replay(cid)
