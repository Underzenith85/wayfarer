"""Independent B376 (Campaigns fourth printing) weapon-on-weapon expectations."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.gurps_melee import defense_value
from wayfarer.orchestration.heavy_parry import HeavyParryResult
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.object_types import ObjectProfile


@pytest.mark.parametrize(
    "weight,quality,die,threshold,broken,stopped",
    [
        (9000, "good", 2, 2, True, True),
        (9000, "good", 3, 2, False, True),
        (11999, "good", 3, 2, False, True),
        (12000, "good", 3, 3, True, True),
        (9000, "cheap", 4, 4, True, True),
        (9000, "fine", 2, 1, False, True),
        (9000, "very-fine", 1, 0, False, True),
        (15000, "cheap", 6, 6, True, True),
        (18000, "cheap", 6, 7, True, False),
    ],
)
async def test_heavy_parry_breakage_and_incoming_damage(
    tmp_path: Path,
    weight: int,
    quality: Literal["cheap", "good", "fine", "very-fine"],
    die: int,
    threshold: int,
    broken: bool,
    stopped: bool,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=weight,
        parry_quality=quality,
        human=True,
    )
    await attack(cid, play)
    # Attack 9 succeeds; Parry 8 succeeds without needing shield DB. 4+1 damage,
    # with no armor, inflicts 5 crushing injury when >6-in-6 sweeps the parry aside.
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3, die, *([] if stopped else [4])])
    result = await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("heavy-parry:"))
    saved = HeavyParryResult.model_validate_json(event.kind)
    assert (saved.breakage_threshold, saved.die, saved.broken, saved.stopped) == (
        threshold,
        die,
        broken,
        stopped,
    )
    assert saved.incoming_weight == weight and saved.weapon_weight == 3000
    assert result.injury and result.injury.injury == (0 if stopped else 5)
    assert result.injury.effect_dice == (die,)
    assert result.injury.adjudication_required is None
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and item.condition.disabled is broken
    assert item.owner_id == "b" and item.equipped and item.ready is not broken
    defender = state.encounters[0].participants[1]
    assert ("sword-b" in defender.ready_item_ids) is not broken
    assert ("sword-b" in dict(defender.hand_bindings)) is not broken
    assert defender.parries == ()  # The next actor is b: its turn resets the parry count.
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_heavy_parry_authority_concurrency_and_restart(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=9000,
        parry_quality="good",
    )
    await attack(cid, play)
    play.rng = RecordedDice([])
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="a")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3, 1])
    results = await asyncio.gather(
        *(
            CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
            for _ in range(2)
        )
    )
    assert results[0] == results[1]
    state = play._load(await play.store.read(cid))
    assert len([e for e in state.resources.events if e.id.startswith("heavy-parry:")]) == 1
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, choice("parry"), authenticated_actor_id="b")
        == results[0]
    )
    assert restarted._load(await restarted.store.read(cid)).resources == state.resources


@pytest.mark.parametrize("weight,available", [(20000, True), (20001, False)])
async def test_basic_lift_limit(tmp_path: Path, weight: int, available: bool) -> None:
    """ST 10 has BL 20 lb; equality is legal, strictly heavier is not."""
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=weight,
        parry_quality="good",
    )
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    if available:
        value, item = defense_value(play, state, defender, "parry")
        assert value and value.value == 10 and item == "sword-b"
    else:
        with pytest.raises(ValidationError, match="No available"):
            defense_value(play, state, defender, "parry")


@pytest.mark.parametrize("first", ["dodge", "parry", "critical-parry"])
async def test_double_defense_applies_breakage_to_the_used_parry(
    tmp_path: Path, first: str
) -> None:
    from test_gurps_maneuvers import defend, turn

    parry_first = first != "dodge"
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=18000 if parry_first else 9000,
        parry_quality="cheap" if parry_first else "good",
        human=True,
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "all_out_defense", defense_option="double")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    if parry_first:
        parry_dice = [1, 1, 1] if first == "critical-parry" else [2, 3, 3]
        # >6-in-6 cancels even a critical parry: no attacker critical-miss roll.
        dice = [3, 3, 3, *parry_dice, 6, 2, 3, 3]
        first_defense, second = "parry", "dodge"
    else:
        dice = [3, 3, 3, 4, 4, 4, 2, 3, 3, 2]
        first_defense, second = "dodge", "parry"
    play.rng = RecordedDice(dice)
    result = await defend(cid, play, "b", first_defense, second_defense=second)
    assert result.injury and result.injury.second_defense
    assert result.injury.second_defense.outcome.succeeded
    assert result.injury.injury == 0 and result.injury.adjudication_required is None
    assert result.injury.critical_table == ()
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and item.condition.disabled and not item.ready
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("attack_dice,defense_dice", [([4, 5, 5], []), ([3, 3, 3], [4, 4, 4])])
async def test_no_breakage_without_successful_contact(
    tmp_path: Path, attack_dice: list[int], defense_dice: list[int]
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=9000,
        parry_quality="good",
    )
    await attack(cid, play)
    play.rng = RecordedDice([*attack_dice, *defense_dice, *([4] if defense_dice else [])])
    await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert not any(e.id.startswith("heavy-parry:") for e in state.resources.events)
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and not item.condition.disabled and item.ready
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("weight,available", [(40000, True), (40001, False)])
async def test_two_handed_basic_lift_limit(tmp_path: Path, weight: int, available: bool) -> None:
    from wayfarer.simulation.gurps_equipment import MeleeMode

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=weight,
        parry_quality="good",
    )
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    combat = play.engine.rules.combat
    assert combat and combat.gurps_equipment
    equipment = combat.gurps_equipment
    entries = tuple(
        e.model_copy(
            update={
                "modes": tuple(
                    m.model_copy(update={"hands": 2}) if isinstance(m, MeleeMode) else m
                    for m in e.modes
                )
            }
        )
        if e.definition_id == "equipment:broadsword"
        else e
        for e in equipment.entries
    )
    play.engine.rules = play.engine.rules.model_copy(
        update={
            "combat": combat.model_copy(
                update={"gurps_equipment": equipment.model_copy(update={"entries": entries})}
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False}) if i.id == "shield-b" else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    defender = state.encounters[0].participants[1]
    if available:
        value, item = defense_value(play, state, defender, "parry")
        assert value and value.value == 9 and item == "sword-b"
    else:
        with pytest.raises(ValidationError, match="No available"):
            defense_value(play, state, defender, "parry")


async def test_shield_db_contact_does_not_break_the_weapon(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        attacker_weight=9000,
        parry_quality="good",
    )
    await attack(cid, play)
    # Parry is 9 without DB; a 10 contacts the shield, not the sword.
    play.rng = RecordedDice([3, 3, 3, 3, 3, 4, 4])
    result = await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.effect_dice == ()
    assert not any(e.id.startswith("heavy-parry:") for e in state.resources.events)
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and not item.condition.disabled and item.ready
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
