"""Entangling ranged attacks (#354): Bolas B181 and Net B211.

The binding facts are test-only pinned catalog metadata, not a catalog
completeness or printing-certification claim. Contest and check numbers follow
the existing quick-contest service (B348-349) and the ranged dispatch documented
in `docs/gurps-ranged.md`.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.ranged import definitions, require_mode
from wayfarer.engine.rules.types.entangle import Entanglement, EntangleSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.encounter import Combatant, RangedSituation
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService

BASIC = "gurps-basic-set-4e-2004"
# A test-only binding: ST 12, -4 to the victim's attacks, -3 to its defenses,
# pinning the legs and staying on the target after the throw.
NET = EntangleSpec(
    binding_st=12, attack_penalty=-4, defense_penalty=-3, immobilizes=True, attached=True
)


def entangling(skill_id: str = "skill:net", **changes: object) -> RangedMode:
    fields: dict[str, object] = {
        "id": "throw",
        "skill_id": skill_id,
        "minimum_st": 10,
        "hands": 1,
        "damage": Damage(basis="fixed", dice=1, damage_type="cr"),
        "accuracy": 0,
        "range_basis": "yards",
        "maximum_range": 10,
        "shots": 1,
        "reload_seconds": 0,
        "bulk": -4,
        "thrown": True,
        "entangle": NET,
    }
    return RangedMode.model_validate(fields | changes)


def scene(distance: float = 2) -> tuple[RangedSituation, ...]:
    return (
        RangedSituation(attacker_id="a", defender_id="b", distance_yards=distance, size_modifier=0),
    )


async def thrown(
    tmp_path: Path, skill_id: str = "skill:net", **changes: object
) -> tuple[str, PlayService]:
    return await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=entangling(skill_id, **changes),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=skill_id, amount=4),),
    )


async def land(cid: str, play: PlayService) -> None:
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="throw")
    play.rng = RecordedDice([3, 3, 4, 2])
    await defend(cid, play, "b")


def attack_penalty_of(state: PlayState, actor: str = "b") -> int:
    from wayfarer.engine.simulation.combat.entangle import attack_penalty

    return attack_penalty(participant(state, actor))


def defense_penalty_of(state: PlayState, actor: str = "b") -> int:
    from wayfarer.engine.simulation.combat.entangle import defense_penalty

    return defense_penalty(participant(state, actor))


def participant(state: PlayState, actor: str = "b") -> Combatant:
    encounter = next(e for e in state.encounters if e.status == "active")
    return next(p for p in encounter.participants if p.actor_id == actor)


def bound(play: PlayService, state: PlayState, actor: str = "b") -> Entanglement | None:
    return participant(state, actor).entangled


async def test_landed_net_binds_the_target_and_stops_its_movement(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    await land(cid, play)
    state = play._load(await play.store.read(cid))
    binding = bound(play, state)
    assert binding is not None
    assert (binding.source_actor_id, binding.mode_id) == ("a", "throw")
    assert (binding.binding_st, binding.attack_penalty, binding.defense_penalty) == (12, -4, -3)
    assert binding.attached and binding.immobilizes and binding.attempts == 0
    # A binding that pins the legs stops movement outright.
    assert movement(play.rules_context, state, "b") == 0
    assert movement(play.rules_context, state, "a") > 0
    # The thrown net still leaves inventory like any other thrown projectile.
    assert "sword-a" not in {i.id for i in state.resources.items}
    assert state.resources.expended_items[0].id == "sword-a"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_a_defended_throw_binds_nothing(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="throw")
    # A dodge that removes the only projectile leaves no binding behind.
    play.rng = RecordedDice([3, 3, 4, 3, 3, 3])
    result = await defend(cid, play, "b", "dodge")
    assert result.injury is not None and result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    assert bound(play, state) is None


async def test_binding_penalises_the_victims_attacks_and_defenses(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    await land(cid, play)
    state = play._load(await play.store.read(cid))
    binding = bound(play, state)
    assert binding is not None
    # The binding's own contribution, separate from anything else in the turn.
    assert (attack_penalty_of(state), defense_penalty_of(state)) == (-4, -3)
    # b answers with its broadsword: skill 13, the binding's -4, and the -2
    # shock from the two points the net itself did.
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    play.rng = RecordedDice([3, 3, 4, 2, 2, 2])
    result = await defend(cid, play, "a")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13 - 4 - 2
    # b's own Dodge carries the binding's -3 through the shared defense service.
    from wayfarer.engine.simulation.combat.melee.defense import defense_value

    state = play._load(await play.store.read(cid))
    dodge, _ = defense_value(play.rules_context, state, participant(state), "dodge")
    # Dodge 8, +1 from b's shield, and the binding's -3.
    assert dodge is not None and int(dodge.value) == 8 + 1 - 3
    assert bound(play, state) is not None


async def test_escape_frees_or_records_the_attempt(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    await land(cid, play)
    # ST 10 against the binding's ST 12: the victim rolls 6, the binding 15.
    play.rng = RecordedDice([2, 2, 2, 5, 5, 5])
    await turn(cid, play, "b", "ready", escape_entanglement=True)
    state = play._load(await play.store.read(cid))
    assert bound(play, state) is None
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_a_lost_contest_keeps_the_binding_and_counts_the_struggle(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    await land(cid, play)
    # The victim rolls 15 against ST 10 and the binding rolls 6 against ST 12.
    play.rng = RecordedDice([5, 5, 5, 2, 2, 2])
    await turn(cid, play, "b", "ready", escape_entanglement=True)
    state = play._load(await play.store.read(cid))
    binding = bound(play, state)
    assert binding is not None and binding.attempts == 1


async def test_escape_requires_its_own_ready_and_an_actual_binding(tmp_path: Path) -> None:
    cid, play = await thrown(tmp_path)
    with pytest.raises(ValidationError, match="Nothing is binding"):
        await turn(cid, play, "a", "ready", escape_entanglement=True)
    await land(cid, play)
    for options in (
        {"maneuver": "attack"},
        {"maneuver": "ready", "item_id": "sword-b"},
        {"maneuver": "ready", "unload_ammunition": True},
    ):
        maneuver = str(options.pop("maneuver"))
        with pytest.raises(ValidationError):
            await turn(cid, play, "b", maneuver, escape_entanglement=True, **options)


def test_catalog_rejects_a_binding_that_is_not_a_single_thrown_weapon() -> None:
    with pytest.raises(SchemaError, match="single thrown bindings"):
        entangling(thrown=False, ammunition_id="equipment:ammo")
    from wayfarer.engine.simulation.equipment.catalog import (
        LITE_EQUIPMENT,
        EquipmentCatalog,
        EquipmentProfile,
    )

    entry = LITE_EQUIPMENT.entries[0].model_copy(update={"modes": (entangling(),)})
    assert isinstance(entry, EquipmentProfile)
    # Bindings are Basic-only catalog metadata.
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        EquipmentCatalog(profile_id="gurps-lite-4e-2004", entries=(entry,))


@pytest.mark.parametrize(
    ("skill_id", "entangle", "expected"),
    [
        ("skill:net", False, "Entangling facts are outside"),
        ("skill:bolas", False, "Entangling facts are outside"),
        ("skill:thrown-weapon-knife", True, "Entangling facts are outside"),
        ("skill:bow", True, "outside the skill's class"),
    ],
)
def test_entangling_facts_belong_to_the_skills_that_bind(
    skill_id: str, entangle: bool, expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        require_mode(
            BASIC,
            skill_id,
            ranged=True,
            thrown=skill_id != "skill:bow",
            ammunition=skill_id == "skill:bow",
            rate_of_fire=1,
            recoil=1,
            hands=1,
            tight_beam=False,
            entangling=entangle,
        )


def test_bolas_and_net_are_dispatched_with_their_recorded_mechanics() -> None:
    from wayfarer.engine.rules.skills.mundane import inventory

    entries = {e.id: e for e in inventory()}
    for identifier, page, difficulty in (
        ("skill:bolas", "B181", "average"),
        ("skill:net", "B211", "hard"),
    ):
        entry = entries[identifier]
        assert entry.bound and entry.dispatch == "combat.ranged-attack"
        assert entry.implementation == "implemented"
        assert entry.definition is not None and entry.definition.skill is not None
        assert entry.definition.skill.reference == page
        assert entry.definition.skill.difficulty.value == difficulty
    bolas = entries["skill:bolas"].definition
    net = entries["skill:net"].definition
    assert bolas is not None and bolas.skill is not None
    assert net is not None and net.skill is not None
    assert bolas.skill.defaults == ()
    assert [(default.target, default.modifier) for default in net.skill.defaults] == [
        ("skill:cloak", -5)
    ]
    # #354 and the defaults gaps are discharged.
    assert entries["skill:bolas"].owners == (344,)
    assert entries["skill:net"].blocker_owners == {}
    assert 354 not in {i for e in entries.values() for i in e.followup_issues}
