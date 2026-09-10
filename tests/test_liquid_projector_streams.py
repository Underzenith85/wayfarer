"""Liquid projector streams (#359): B205.

Stream facts are test-only pinned catalog metadata and claim no catalog
completeness. A stream is held second by second: every second is paid for in
ammunition and rolled separately, it can be walked to another target, and it
ends when its holder stops pouring or the projector's ceiling is reached.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.character.compiler import Purchase
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.mundane_skills import audit_report, inventory
from wayfarer.rules.mundane_skills.ranged import PROCEDURES, definitions, ranged_scope, require_mode
from wayfarer.rules.spray_types import SprayerSpec, Stream
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, RangedSituation
from wayfarer.simulation.gurps_equipment import Damage, RangedMode

BASIC = "gurps-basic-set-4e-2004"
SPECIALTIES = tuple(
    f"skill:liquid-projector-{key}"
    for key in ("flamethrower", "sprayer", "squirt-gun", "water-cannon")
)
# A projector that runs for three seconds and burns two rounds a second.
SPEC = SprayerSpec(sustained_seconds=3, rounds_per_second=2, ignites=True)


def projector(skill_id: str, spec: SprayerSpec = SPEC, **changes: object) -> RangedMode:
    fields: dict[str, object] = {
        "id": "stream",
        "skill_id": skill_id,
        "minimum_st": 9,
        "hands": 2,
        "damage": Damage(basis="fixed", dice=1, damage_type="burn"),
        "accuracy": 1,
        "range_basis": "yards",
        "maximum_range": 20,
        "shots": 10,
        "rate_of_fire": 1,
        "recoil": 1,
        "reload_seconds": 3,
        "bulk": -6,
        "ammunition_id": "equipment:ammo",
        "sprayer": spec,
    }
    return RangedMode.model_validate(fields | changes)


def scene() -> tuple[RangedSituation, ...]:
    return (RangedSituation(attacker_id="a", defender_id="b", distance_yards=2, size_modifier=0),)


def actor(state: PlayState, who: str = "a") -> Combatant:
    encounter = next(e for e in state.encounters if e.status == "active")
    return next(p for p in encounter.participants if p.actor_id == who)


async def loaded(
    tmp_path: Path, skill_id: str, spec: SprayerSpec = SPEC
) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=projector(skill_id, spec),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=skill_id, amount=4),),
        campaign_technology_level=2,
    )
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="stream",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
    return cid, play


async def pour(cid: str, play: PlayService, target: str = "b") -> None:
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id=target, mode_id="stream")
    play.rng = RecordedDice([3, 3, 4, 2])
    await defend(cid, play, target)


def test_the_family_expands_and_publishes_what_it_does_not_carry() -> None:
    entries = {e.id: e for e in inventory()}
    family = PROCEDURES["skill:liquid-projector"]
    assert family.implemented and not family.dispatchable
    assert family.specialties == SPECIALTIES
    assert entries["skill:liquid-projector"].owners == (344,)
    for identifier in SPECIALTIES:
        entry = entries[identifier]
        assert entry.bound and entry.dispatch == "combat.ranged-attack"
        assert entry.definition is not None and entry.definition.skill is not None
        assert [(d.target, d.modifier) for d in entry.definition.skill.defaults] == [
            ("attribute:dx", -4)
        ]
    # A bound row can still leave named scope to another issue; it is published,
    # not folded back into a blocker.
    report = audit_report()["transferred_procedure_scope"]
    assert isinstance(report, list)
    published = {
        (row["skill"], row["id"], row["owner_issue"])
        for row in report
        if str(row["skill"]).startswith("skill:liquid-projector")
    }
    assert ("skill:liquid-projector", "lingering-fire", 398) in published
    assert ("skill:liquid-projector", "simultaneous-area-coverage", 398) in published
    assert all(scope.owner_issue == 398 for _, scope in ranged_scope())


@pytest.mark.parametrize("identifier", SPECIALTIES)
async def test_every_specialty_opens_a_stream(tmp_path: Path, identifier: str) -> None:
    cid, play = await loaded(tmp_path, identifier)
    await pour(cid, play)
    state = play._load(await play.store.read(cid))
    stream = actor(state).stream
    assert stream is not None
    assert (stream.mode_id, stream.target_id, stream.seconds) == ("stream", "b", 1)
    assert stream.sustained_seconds == 3 and stream.ignites
    # One second of stream costs its pinned rounds, not one shot.
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 8
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_a_held_stream_is_paid_for_every_second_and_can_be_walked(tmp_path: Path) -> None:
    cid, play = await loaded(tmp_path, "skill:liquid-projector-flamethrower", SPEC)
    await pour(cid, play)
    await turn(cid, play, "b", "do_nothing")
    await pour(cid, play)
    state = play._load(await play.store.read(cid))
    stream = actor(state).stream
    assert stream is not None and stream.seconds == 2
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 6
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_a_stream_stops_at_its_ceiling(tmp_path: Path) -> None:
    cid, play = await loaded(
        tmp_path,
        "skill:liquid-projector-flamethrower",
        SprayerSpec(sustained_seconds=2, rounds_per_second=1),
    )
    for _ in range(2):
        await pour(cid, play)
        await turn(cid, play, "b", "do_nothing")
    with pytest.raises(ValidationError, match="as long as it can be held"):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="stream")


async def test_letting_go_releases_the_stream(tmp_path: Path) -> None:
    cid, play = await loaded(tmp_path, "skill:liquid-projector-sprayer")
    await pour(cid, play)
    assert actor(play._load(await play.store.read(cid))).stream is not None
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert actor(state).stream is None
    # Having let go, the firer opens a fresh stream rather than resuming one.
    await turn(cid, play, "b", "do_nothing")
    await pour(cid, play)
    stream = actor(play._load(await play.store.read(cid))).stream
    assert stream is not None and stream.seconds == 1


def test_stream_facts_belong_only_to_the_liquid_projector_rows() -> None:
    def check(skill_id: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": False,
            "ammunition": True,
            "rate_of_fire": 1,
            "recoil": 1,
            "hands": 2,
            "tight_beam": False,
            "spraying": True,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, skill_id, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    assert "Stream facts are outside" in check("skill:guns-rifle", hands=1)
    assert "Stream facts are outside" in check("skill:liquid-projector-sprayer", spraying=False)
    assert "concrete specialty" in check("skill:liquid-projector")


def test_a_stream_is_never_thrown_bound_or_rapid_fired() -> None:
    with pytest.raises(SchemaError, match="single held discharges"):
        projector("skill:liquid-projector-sprayer", rate_of_fire=3)
    with pytest.raises(SchemaError, match="ceiling"):
        Stream(weapon_id="w", mode_id="m", target_id="b", seconds=4, sustained_seconds=3)
