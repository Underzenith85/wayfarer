"""Independent Campaigns fourth-printing B428-B437 environmental cases (#517)."""

from pathlib import Path

import pytest
from test_medical_service import setup
from test_objects import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environmental_hazards import (
    CombustionFacts,
    acceleration_spec,
    acid_spec,
    atmosphere_spec,
    decay_radiation,
    electricity_spec,
    ignition_threshold,
    intense_heat_spec,
    pressure_spec,
    radiation_spec,
    seasickness_spec,
    thermal_shock_spec,
    vacuum_spec,
)
from wayfarer.engine.rules.types.hazard import HazardProtection, HazardSchedule, HazardSpec
from wayfarer.engine.simulation.equipment.objects import DamageObject
from wayfarer.engine.simulation.events import ActorAudience, play_facts
from wayfarer.engine.simulation.health.environmental_objects import apply_burning_object
from wayfarer.engine.simulation.health.hazards import HazardCommand, HazardResult, apply_hazard
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def _schedule(spec: HazardSpec, *, symptoms: int = 0) -> HazardSchedule:
    return HazardSchedule(
        id="exposure",
        actor_id="a",
        spec=spec,
        started=0,
        due=0,
        remaining=spec.cycles,
        ht=10,
        will=10,
        swimming=10,
        full_hp=5,
        symptoms=symptoms,
        no_air_since=0 if spec.kind == "vacuum" else None,
    )


async def _resolve(
    tmp_path: Path, spec: HazardSpec, dice: list[int], *, symptoms: int = 0
) -> tuple[ResourceState, HazardResult]:
    cid, play, _ = await setup(tmp_path)
    before = play._load(await play.store.read(cid))
    schedule = _schedule(spec, symptoms=symptoms)
    resources = before.resources.model_copy(update={"hazards": (schedule,)})
    return apply_hazard(
        resources,
        HazardCommand(
            id="tick", actor_id="a", expected_revision=0, kind="resolve", hazard_id=spec.id
        ),
        schedule,
        rng=RecordedDice(dice),
        system=True,
    )


@pytest.mark.parametrize(
    ("variant", "interval", "dice", "add"),
    [("splash", 1, 1, -3), ("immersion", 1, 1, -1), ("swallowed", 900, 0, 1)],
)
def test_acid_profiles_are_pinned(variant: str, interval: int, dice: int, add: int) -> None:
    spec = acid_spec(
        variant,  # type: ignore[arg-type]
        id="acid",
        scene_id="lab",
        protection=HazardProtection(),
    )
    assert (spec.interval, spec.damage_dice, spec.damage_add, spec.damage_type) == (
        interval,
        dice,
        add,
        "cor",
    )
    assert spec.cycles_dice == (3 if variant == "swallowed" else 0)


async def test_acid_and_corrosive_air_protection_boundaries(tmp_path: Path) -> None:
    exposed = acid_spec("splash", id="acid", scene_id="lab", protection=HazardProtection())
    _, result = await _resolve(tmp_path, exposed, [4])
    assert result.hp_lost == 1
    sealed = exposed.model_copy(update={"protection": HazardProtection(sealed=True)})
    _, result = await _resolve(tmp_path, sealed, [])
    assert result.hp_lost == 0 and not result.active

    air = atmosphere_spec(
        "corrosive-trace",
        id="air",
        scene_id="plant",
        severity=4,
        duration=120,
        protection=HazardProtection(),
    )
    state, result = await _resolve(tmp_path, air, [6, 6, 6], symptoms=3)
    assert result.hp_lost == 1 and result.conditions == ("blindness", "coughing")
    assert set(state.hazards[0].conditions) == {"coughing", "blindness"}


def test_atmosphere_rejects_unlisted_strengths_and_authors_air_supply() -> None:
    with pytest.raises(ValidationError, match="HT-2 through HT-6"):
        atmosphere_spec(
            "toxic-lethal",
            id="gas",
            scene_id="plant",
            severity=1,
            duration=60,
            protection=HazardProtection(),
        )
    suffocating = atmosphere_spec(
        "suffocating",
        id="nitrogen",
        scene_id="plant",
        severity=1,
        duration=10,
        protection=HazardProtection(breathing_supply=True),
    )
    assert suffocating.kind == "vacuum" and suffocating.environment is not None
    assert suffocating.protection is not None and suffocating.protection.breathing_supply


async def test_cold_and_heat_protection_boundaries(tmp_path: Path) -> None:
    shock = thermal_shock_spec(id="ice", scene_id="sea", duration=60, dry_suit=False)
    _, result = await _resolve(tmp_path, shock, [3, 3, 3])
    assert result.fp_lost == 1
    dry = thermal_shock_spec(id="ice", scene_id="sea", duration=60, dry_suit=True)
    _, result = await _resolve(tmp_path, dry, [])
    assert result.fp_lost == 0 and not result.active
    heat = intense_heat_spec(
        id="furnace",
        scene_id="forge",
        dr=3,
        duration=4,
        protection=HazardProtection(insulated=False),
    )
    assert heat.delay == 9 and heat.interval == 1


async def test_electricity_insulation_stun_and_lethal_damage(tmp_path: Path) -> None:
    shock = electricity_spec(
        "nonlethal",
        id="fence",
        scene_id="yard",
        strength_modifier=-3,
        protection=HazardProtection(),
    )
    state, result = await _resolve(tmp_path, shock, [5, 5, 5])
    hp = next(p for p in state.pools if p.id == "hp:a")
    assert result.conditions == ("stunned",) and hp.injury is not None and hp.injury.stunned
    insulated = shock.model_copy(update={"protection": HazardProtection(insulated=True)})
    _, result = await _resolve(tmp_path, insulated, [])
    assert result.conditions == () and not result.active
    lethal = electricity_spec(
        "localized",
        id="main",
        scene_id="yard",
        strength_modifier=0,
        damage_dice=2,
        protection=HazardProtection(),
    )
    _, result = await _resolve(tmp_path, lethal, [1, 1, 3, 3, 3])
    assert result.hp_lost == 2
    bodywide = electricity_spec(
        "lethal",
        id="lightning",
        scene_id="yard",
        strength_modifier=0,
        damage_dice=2,
        protection=HazardProtection(),
    )
    state, result = await _resolve(tmp_path, bodywide, [1, 1, 5, 5, 5])
    fp = next(p for p in state.pools if p.id == "fp:a")
    assert result.conditions == ("unconscious", "heart-attack")
    assert fp.fatigue is not None and fp.fatigue.heart_attack


def test_fire_ignition_thresholds_include_tight_beam_and_nonflammable() -> None:
    assert ignition_threshold("highly-flammable") == 1
    assert ignition_threshold("flammable") == 3
    assert ignition_threshold("resistant", tight_beam=True) == 100
    assert ignition_threshold("nonflammable") is None


def test_fire_object_damage_and_ignition_share_one_receipt() -> None:
    engine, state = fixture()
    command = DamageObject(
        id="flame",
        actor_id="a",
        expected_revision=0,
        item_id="sword",
        basic_damage=10,
        damage_type="burn",
    )
    state, result = apply_burning_object(
        engine,
        state,
        command,
        CombustionFacts(material="resistant"),
        rng=RecordedDice([]),
    )
    assert result.ignited and result.damage.injury == 8 and state.revision == 1
    replayed, duplicate = apply_burning_object(
        engine,
        state,
        command,
        CombustionFacts(material="resistant"),
        rng=RecordedDice([]),
    )
    assert replayed == state and duplicate == result


async def test_acceleration_and_pressure_use_margin_and_support(tmp_path: Path) -> None:
    acceleration = acceleration_spec(
        id="launch",
        scene_id="capsule",
        centigravity=500,
        home_centigravity=100,
        duration=1,
        posture="upright",
        protection=HazardProtection(),
    )
    _, result = await _resolve(tmp_path, acceleration, [4, 4, 4])
    assert result.fp_lost == 4
    crushing = pressure_spec(
        "crushing",
        id="deep",
        scene_id="ocean",
        pressure_milli_atmospheres=20000,
        duration=60,
        protection=HazardProtection(),
    )
    _, result = await _resolve(tmp_path, crushing, [4, 4, 4])
    assert result.hp_lost == 1
    supported = crushing.model_copy(update={"protection": HazardProtection(pressure_support=3)})
    _, result = await _resolve(tmp_path, supported, [])
    assert result.hp_lost == 0 and not result.active


async def test_radiation_dose_pf_table_decay_and_replay(tmp_path: Path) -> None:
    unshielded = radiation_spec(
        id="reactor",
        scene_id="core",
        rads=200,
        interval=3600,
        cycles=1,
        protection=HazardProtection(radiation_pf=1),
    )
    state, first_result = await _resolve(tmp_path, unshielded, [2, 2, 2])
    assert first_result.radiation_dose == 200 and first_result.conditions == ("radiation-b",)
    saved = state.hazards[0]
    assert decay_radiation(saved, at=30 * 86400).radiation_dose == 200
    decayed = decay_radiation(saved, at=50 * 86400)
    assert decayed.radiation_dose == 20
    assert decay_radiation(decayed, at=50 * 86400).radiation_dose == 20
    shielded = unshielded.model_copy(update={"protection": HazardProtection(radiation_pf=100)})
    _, result = await _resolve(tmp_path, shielded, [3, 3, 3])
    assert result.radiation_dose == 2 and result.conditions == ()
    replayed, duplicate = apply_hazard(
        state,
        HazardCommand(
            id="tick", actor_id="a", expected_revision=0, kind="resolve", hazard_id="reactor"
        ),
        saved,
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == state and duplicate == first_result


async def test_seasickness_and_vacuum_protection_boundaries(tmp_path: Path) -> None:
    seasick = seasickness_spec(
        id="roll", scene_id="ship", duration=86400, protection=HazardProtection()
    )
    _, result = await _resolve(tmp_path, seasick, [6, 6, 6])
    assert result.check is not None and result.conditions == ("retching",)
    stable = seasick.model_copy(update={"protection": HazardProtection(motion_stabilized=True)})
    _, result = await _resolve(tmp_path, stable, [])
    assert result.check is None and not result.active
    vacuum = vacuum_spec(
        id="void",
        scene_id="airlock",
        blood_oxygen_seconds=20,
        explosive=False,
        protection=HazardProtection(),
    )
    _, result = await _resolve(tmp_path, vacuum, [])
    assert result.fp_lost == 1
    protected = vacuum.model_copy(update={"protection": HazardProtection(vacuum_support=True)})
    _, result = await _resolve(tmp_path, protected, [])
    assert result.fp_lost == 0 and not result.active


async def test_hazard_event_is_scoped_to_affected_actor(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    before = play._load(await play.store.read(cid))
    spec = acid_spec(
        "splash", id="hidden-source", scene_id="secret-lab", protection=HazardProtection()
    )
    schedule = _schedule(spec)
    resources = before.resources.model_copy(update={"hazards": (schedule,)})
    resources, _ = apply_hazard(
        resources,
        HazardCommand(
            id="private", actor_id="a", expected_revision=0, kind="resolve", hazard_id=spec.id
        ),
        schedule,
        rng=RecordedDice([4]),
        system=True,
    )
    after = before.model_copy(update={"resources": resources, "revision": resources.revision})
    events = play_facts(before, after, "a")
    hazard = next(event for event in events if event.kind == "hazard.resolved")
    assert hazard.audience == ActorAudience(actor_ids=("a",))
    assert (
        "secret-lab" not in hazard.model_dump_json()
        and "hidden-source" not in hazard.model_dump_json()
    )
