"""Crew-served and vehicle-mounted ranged weapon skills (#357).

Artillery B178 and Gunner B198. Mount facts are test-only pinned catalog
metadata and claim no catalog completeness. A mount bears the weapon, so the
firer's grip and ST do not validate the shot; the crew does. An indirectly laid
shot arrives without warning and is not actively defended.
"""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.ranged import PROCEDURES, definitions, require_mode
from wayfarer.engine.rules.types.mount import MountSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.combat.combat import RangedSituation
from wayfarer.engine.simulation.combat.commands import HexPlacement, MigrateEncounterHex
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield, Pose
from wayfarer.engine.world import Fact
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService

BASIC: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
ARTILLERY = tuple(
    f"skill:artillery-{key}"
    for key in ("beams", "bombs", "cannon", "catapult", "guided-missile", "torpedoes")
)
GUNNER = tuple(
    f"skill:gunner-{key}" for key in ("beams", "cannon", "machine-gun", "rockets", "torpedoes")
)
# A two-person direct-fire mount, and a three-person mount laid indirectly.
DIRECT = MountSpec(crew=2, laying_seconds=0, vehicle_mounted=True)
INDIRECT = MountSpec(crew=3, laying_seconds=4, indirect=True)


def mounted(skill_id: str, spec: MountSpec = DIRECT, **changes: object) -> RangedMode:
    fields: dict[str, object] = {
        "id": "shot",
        "skill_id": skill_id,
        # A firer far weaker than the weapon: the mount is what bears it.
        "minimum_st": 30,
        "hands": 2,
        "damage": Damage(basis="fixed", dice=1, damage_type="cr"),
        "accuracy": 3,
        "range_basis": "yards",
        "maximum_range": 500,
        "half_damage_range": 200,
        "shots": 4,
        "rate_of_fire": 1,
        "recoil": 1,
        "reload_seconds": 3,
        "bulk": -8,
        "ammunition_id": "equipment:ammo",
        "mount": spec,
    }
    return RangedMode.model_validate(fields | changes)


def scene() -> tuple[RangedSituation, ...]:
    return (RangedSituation(attacker_id="a", defender_id="b", distance_yards=2, size_modifier=0),)


async def emplaced(
    tmp_path: Path, skill_id: str, spec: MountSpec = DIRECT
) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        third_actor=True,
        ranged_mode=mounted(skill_id, spec),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=skill_id, amount=4),),
        campaign_technology_level=2,
    )
    crew = ("a", "c") if spec.crew == 2 else ("a", "b", "c")
    await turn(cid, play, "a", "ready", item_id="sword-a", mount_crew=crew)
    for other in ("b", "c"):
        await turn(cid, play, other, "do_nothing")
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="shot",
            reload_ammunition_id="ammo-a",
        )
        for other in ("b", "c"):
            await turn(cid, play, other, "do_nothing")
    return cid, play


def test_both_families_expand_into_their_indexed_specialties() -> None:
    entries = {e.id: e for e in inventory()}
    for family, expanded in (("skill:artillery", ARTILLERY), ("skill:gunner", GUNNER)):
        procedure = PROCEDURES[family]
        assert procedure.implemented and not procedure.dispatchable
        assert procedure.specialties == expanded
        assert procedure.blockers == () and entries[family].owners == (344,)
    dispatched = {d.id for d in definitions()}
    assert set(ARTILLERY) | set(GUNNER) <= dispatched
    # B178 Artillery is IQ-based and has no cross-specialty defaults. B198
    # Gunner is DX-based and its specialties default to one another at -4.
    for identifier, attribute, default in (
        *((row, "attribute:iq", -5) for row in ARTILLERY),
        *((row, "attribute:dx", -4) for row in GUNNER),
    ):
        entry = entries[identifier]
        assert entry.bound and entry.dispatch == "combat.ranged-attack"
        assert entry.definition is not None and entry.definition.skill is not None
        assert entry.definition.skill.attribute.value == attribute
        defaults = entry.definition.skill.defaults
        assert (defaults[0].target, defaults[0].modifier) == (attribute, default)
        assert len(defaults) == (1 if identifier in ARTILLERY else 5)
        assert "conditional-or-skill-defaults" not in entry.blockers


@pytest.mark.parametrize("identifier", [*ARTILLERY, *GUNNER])
async def test_every_mounted_specialty_fires_from_its_crew(tmp_path: Path, identifier: str) -> None:
    cid, play = await emplaced(tmp_path, identifier)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    play.rng = RecordedDice([3, 3, 4, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # IQ/DX 10 with four points is 12 for an Easy skill and 11 for an Average
    # one. The mount bears the weapon, so its ST 30 costs the firer nothing.
    expected = 11 if identifier.startswith("skill:artillery-") else 12
    assert result.injury.attack.effective_target == expected
    assert result.injury.hits == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_vehicle_mount_uses_vehicle_pose_control_penalty_and_occupant_cover(
    tmp_path: Path,
) -> None:
    cid, play = await emplaced(tmp_path, "skill:gunner-cannon")
    loaded = play._load(await play.store.read(cid))

    def add_vehicles(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        resources = state.resources.model_copy(
            update={
                "transports": (
                    Transport(
                        id="gun-carrier",
                        mechanics_version=2,
                        locomotion="ground-wheeled",
                        body_id="sword-a",
                        operator_id="a",
                        occupants=("a", "c"),
                        acceleration=3,
                        top_speed=20,
                        attack_penalty=-2,
                        aim_lost=True,
                    ),
                    Transport(
                        id="target-carrier",
                        mechanics_version=2,
                        locomotion="ground-wheeled",
                        body_id="sword-b",
                        operator_id="b",
                        occupants=("b",),
                        acceleration=3,
                        top_speed=20,
                        q=1,
                        facing=3,
                        occupant_cover_dr=4,
                    ),
                )
            }
        )
        state = state.model_copy(
            update={
                "resources": resources,
                "world": replace(
                    state.world,
                    facts=(
                        *state.world.facts,
                        Fact("vehicle-seen-b", "b", "visible", "yes"),
                    ),
                    knowledge=(*state.world.knowledge, ("a", "vehicle-seen-b")),
                ),
            }
        )
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="resource", outcome="vehicle poses")

    await play.store.commit_turn(cid, "vehicles", loaded.revision, "vehicles", add_vehicles)
    current = play._load(await play.store.read(cid))
    board = HexBattlefield(
        id=current.encounters[0].battlefield_id,
        coordinate_system="hex-axial-v1",
        profile_id=BASIC,
        baseline_id=BASELINE_ID,
        cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(-2, 4) for r in range(-2, 3)),
    )
    await CombatService(play).execute(
        cid,
        MigrateEncounterHex(
            id="vehicle-hex",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="fight",
            battlefield=board,
            placements=(
                HexPlacement(actor_id="a", pose=Pose(position=Hex(q=0, r=0), facing=0)),
                HexPlacement(actor_id="b", pose=Pose(position=Hex(q=1, r=0), facing=3)),
                HexPlacement(actor_id="c", pose=Pose(position=Hex(q=-1, r=0), facing=0)),
            ),
        ),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="shot",
        transport_id="gun-carrier",
    )
    pending = play._load(await play.store.read(cid))
    carrier = next(t for t in pending.resources.transports if t.id == "gun-carrier")
    assert (carrier.attack_penalty, carrier.aim_lost) == (0, False)
    assert pending.encounters[0].pending_defense is not None
    assert pending.encounters[0].pending_defense.transport_id == "gun-carrier"
    play.rng = RecordedDice([3, 3, 4, 6])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 10
    assert result.injury.resistance == 4
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_an_indirect_mount_leaves_the_target_no_active_defense(tmp_path: Path) -> None:
    cid, play = await emplaced(tmp_path, "skill:artillery-cannon", INDIRECT)
    result = await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    assert result.available == ("none",)
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", "dodge")


async def test_an_unserved_mount_cannot_be_fired(tmp_path: Path) -> None:
    """No crew, no shot: the mount is not a weapon the gunner simply holds."""
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        third_actor=True,
        ranged_mode=mounted("skill:gunner-cannon"),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id="skill:gunner-cannon", amount=4),),
        campaign_technology_level=2,
    )
    with pytest.raises(ValidationError, match="fired by its own crew"):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    # Once served, the same mount fires, so the refusal was the crew and not
    # the weapon, the skill or the era.
    await turn(cid, play, "a", "ready", item_id="sword-a", mount_crew=("a", "c"))
    for other in ("b", "c"):
        await turn(cid, play, other, "do_nothing")
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="shot",
            reload_ammunition_id="ammo-a",
        )
        for other in ("b", "c"):
            await turn(cid, play, other, "do_nothing")
    result = await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    assert result.pending_defense_id is not None


async def test_crew_assignment_is_validated_and_durable(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        third_actor=True,
        ranged_mode=mounted("skill:gunner-machine-gun"),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id="skill:gunner-machine-gun", amount=4),),
        campaign_technology_level=2,
    )
    for crew, expected in (
        (("a",), "full crew"),
        (("b", "c"), "gunner serves its own mount"),
        (("a", "a"), "serves a mount once"),
    ):
        with pytest.raises(ValidationError, match=expected):
            await turn(cid, play, "a", "ready", item_id="sword-a", mount_crew=crew)
    await turn(cid, play, "a", "ready", item_id="sword-a", mount_crew=("a", "c"))
    state = play._load(await play.store.read(cid))
    assert next(i.mount_crew for i in state.resources.items if i.id == "sword-a") == ("a", "c")
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_mount_facts_belong_only_to_the_skills_that_are_served() -> None:
    def check(skill_id: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": False,
            "ammunition": True,
            "rate_of_fire": 1,
            "recoil": 1,
            "hands": 2,
            "tight_beam": False,
            "mounted": True,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, skill_id, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    assert "Mount facts are outside" in check("skill:bow")
    assert "Mount facts are outside" in check("skill:guns-rifle", hands=1)
    assert "Mount facts are outside" in check("skill:gunner-cannon", mounted=False)


def test_a_mount_is_never_thrown_or_entangling() -> None:
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError, match="neither thrown nor entangling"):
        mounted(
            "skill:gunner-cannon",
            thrown=True,
            ammunition_id=None,
            shots=1,
            rate_of_fire=1,
            reload_seconds=0,
        )
    with pytest.raises(SchemaError, match="requires laying time"):
        MountSpec(crew=2, laying_seconds=0, indirect=True)
    with pytest.raises(SchemaError, match="belongs to indirect fire"):
        MountSpec(crew=2, laying_seconds=3)
