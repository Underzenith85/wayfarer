"""Independent numeric cases: Campaigns fourth printing B379,399,421-422,552.

These examples are not a certification of the frozen source baseline.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_gurps_melee import choice, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import (
    HitLocation,
    HumanBody,
    HumanLocation,
    appearance_levels_lost,
    deafened,
    disabled_locations,
)
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.hit_locations import attack_penalty, select_location
from wayfarer.engine.simulation.health.injury import (
    DisableLocation,
    ResolveCrippling,
    Wound,
    apply_injury,
    apply_location_effect,
)
from wayfarer.engine.simulation.resources import Item, Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def human(hp: int = 10) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=hp,
                maximum=10,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human"),
            ),
        )
    )


def wound(
    location: HumanLocation,
    damage: int,
    kind: DamageType = "cut",
    dr: int = 0,
    divisor: Decimal = Decimal(1),
) -> Wound:
    return Wound(
        id="hit",
        actor_id="a",
        expected_revision=0,
        location=location,
        basic_damage=damage,
        damage_type=kind,
        resistance=dr,
        armor_divisor=divisor,
    )


@pytest.mark.parametrize(
    "location,damage,kind,dr,divisor,injury,effective",
    [
        ("right-arm", 9, "cut", 0, "1", 6, 0),
        ("left-hand", 7, "imp", 0, "1", 4, 0),
        ("vitals", 3, "imp", 0, "1", 9, 0),
        ("skull", 5, "cr", 0, "1", 12, 2),
        ("right-eye", 2, "imp", 0, "1", 8, 0),
        ("neck", 5, "cut", 1, "1", 8, 1),
        ("torso", 6, "cr", 5, "2", 4, 2),
        ("skull", 6, "cr", 5, "2", 12, 3),
        ("torso", 6, "cr", 0, "0.5", 5, 1),
        ("skull", 3, "tox", 0, "1", 3, 0),
    ],
)
def test_golden_wounding_and_armor(
    location: HumanLocation,
    damage: int,
    kind: DamageType,
    dr: int,
    divisor: str,
    injury: int,
    effective: int,
) -> None:
    updated, result = apply_injury(
        human(),
        wound(location, damage, kind, dr, Decimal(divisor)),
        ht=20,
        rng=RecordedDice([1, 1, 1] * 4),
        system=True,
    )
    assert result.injury == injury
    assert result.effective_resistance == effective
    assert updated.pools[0].current == 10 - injury


def test_crippling_is_pending_then_temporary_until_full_hp() -> None:
    state, result = apply_injury(
        human(), wound("right-arm", 5), ht=12, rng=RecordedDice([3, 3, 3]), system=True
    )
    status = state.pools[0].injury
    assert status and status.lasting_injuries[0].kind == "crippled"
    assert status.lasting_injuries[0].duration == "pending"
    assert result.uncapped_injury == 7 and result.injury == 6
    command = ResolveCrippling(
        id="duration", actor_id="a", expected_revision=1, injury_id=result.lasting_injury_ids[0]
    )
    state, resolved = apply_injury(state, command, ht=12, rng=RecordedDice([3, 3, 3]), system=True)
    status = state.pools[0].injury
    assert status and status.lasting_injuries[0].duration == "temporary"
    assert disabled_locations(status.lasting_injuries, now=10000000, full_hp=False) == {"right-arm"}
    assert not disabled_locations(status.lasting_injuries, now=10000000, full_hp=True)
    reloaded = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_injury(reloaded, command, ht=12, rng=RecordedDice([]), system=True) == (
        state,
        resolved,
    )
    with pytest.raises(ConflictError):
        apply_injury(
            reloaded,
            command.model_copy(update={"physician_tl": 7}),
            ht=12,
            rng=RecordedDice([]),
            system=True,
        )


@pytest.mark.parametrize(
    "dice,tl,duration,months",
    [
        ([5, 5, 5, 5], None, "lasting", 5),
        ([5, 5, 5, 5], 5, "lasting", 4),
        ([5, 5, 5, 5], 6, "lasting", 3),
        ([5, 5, 5, 5], 7, "lasting", 2),
        ([5, 5, 5, 1], 12, "lasting", 1),
        ([6, 6, 6], None, "permanent", 0),
    ],
)
def test_lasting_duration(dice: list[int], tl: int | None, duration: str, months: int) -> None:
    state, result = apply_injury(
        human(), wound("left-foot", 3), ht=12, rng=RecordedDice([3, 3, 3]), system=True
    )
    state, _ = apply_injury(
        state,
        ResolveCrippling(
            id="end",
            actor_id="a",
            expected_revision=1,
            injury_id=result.lasting_injury_ids[0],
            physician_tl=tl,
        ),
        ht=12,
        rng=RecordedDice(dice),
        system=True,
    )
    assert state.pools[0].injury
    lasting = state.pools[0].injury.lasting_injuries[0]
    assert lasting.duration == duration
    assert lasting.recovery_at == (months * 30 * 86400 if months else None)
    if months:
        assert lasting.recovery_at is not None
        assert lasting.active(now=lasting.recovery_at - 1, full_hp=True)
        assert not lasting.active(now=months * 30 * 86400, full_hp=True)


def test_severing_and_funny_bone_thresholds() -> None:
    state, result = apply_injury(
        human(), wound("right-arm", 8), ht=12, rng=RecordedDice([3, 3, 3]), system=True
    )
    assert result.injury == 6 and result.uncapped_injury == 12
    assert state.pools[0].injury and state.pools[0].injury.lasting_injuries[0].kind == "severed"
    state, result = apply_injury(
        human(),
        wound("right-hand", 1, "cr"),
        ht=12,
        rng=RecordedDice([]),
        system=True,
        funny_bone=True,
        double_shock=True,
    )
    assert result.injury == 1 and state.pools[0].injury
    lasting = state.pools[0].injury.lasting_injuries[0]
    assert lasting.duration == "timed" and lasting.recovery_at == 4
    assert lasting.active(now=3, full_hp=True) and not lasting.active(now=4, full_hp=False)


def test_critical_head_deafness_is_durable_and_uses_crippling_duration() -> None:
    state, result = apply_injury(
        human(),
        wound("skull", 3, "cr"),
        ht=12,
        rng=RecordedDice([3, 3, 3]),
        system=True,
        head_trauma="deafened",
    )
    status = state.pools[0].injury
    assert status and result.lasting_injury_ids == ("hit:head-trauma:deafened",)
    assert deafened(status.lasting_injuries, now=0, full_hp=False)
    assert "skull" not in disabled_locations(status.lasting_injuries, now=0, full_hp=False)

    state, _ = apply_injury(
        state,
        ResolveCrippling(
            id="hearing-duration",
            actor_id="a",
            expected_revision=1,
            injury_id=result.lasting_injury_ids[0],
        ),
        ht=12,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    reloaded = ResourceState.model_validate_json(state.model_dump_json())
    status = reloaded.pools[0].injury
    assert status and not deafened(status.lasting_injuries, now=0, full_hp=True)


@pytest.mark.parametrize(("kind", "levels"), [("cut", 1), ("burn", 2), ("cor", 2)])
def test_critical_head_scarring_persists_and_supplies_appearance_loss(
    kind: DamageType, levels: int
) -> None:
    state, result = apply_injury(
        human(),
        wound("face", 2, kind),
        ht=12,
        rng=RecordedDice([3, 3, 3]),
        system=True,
        head_trauma="scarred",
        scar_levels=levels,
    )
    status = state.pools[0].injury
    assert status and result.lasting_injury_ids == ("hit:head-trauma:scarred",)
    assert appearance_levels_lost(status.lasting_injuries, now=10**9, full_hp=True) == levels
    assert ResourceState.model_validate_json(state.model_dump_json()) == state


@pytest.mark.parametrize(
    "dice,expected",
    [
        ([1, 1, 1], "skull"),
        ([1, 2, 2], "face"),
        ([2, 2, 2], "right-leg"),
        ([2, 3, 3], "right-arm"),
        ([3, 3, 3], "torso"),
        ([3, 4, 4], "groin"),
        ([4, 4, 4], "left-arm"),
        ([4, 4, 5], "left-leg"),
        ([5, 5, 5, 1], "right-hand"),
        ([5, 5, 5, 6], "left-hand"),
        ([5, 5, 6, 1], "right-foot"),
        ([5, 6, 6], "neck"),
    ],
)
def test_random_location_table(dice: list[int], expected: HumanLocation) -> None:
    selected, recorded = select_location("random", rng=RecordedDice(dice))
    assert selected == expected and recorded == tuple(dice)
    assert select_location("random", rng=RecordedDice([1, 2, 2]), from_behind=True)[0] == "skull"


def test_profile_anatomy_and_authority_are_explicit() -> None:
    with pytest.raises(SchemaError):
        HumanBody.model_validate({"anatomy": "octopus"})
    state = human()
    with pytest.raises(ValidationError, match="authority"):
        apply_injury(state, wound("skull", 1), ht=12, rng=RecordedDice([]))
    pool = state.pools[0]
    absent = state.model_copy(
        update={
            "pools": (
                pool.model_copy(
                    update={"injury": InjuryStatus(profile_id="gurps-basic-set-4e-2004")}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="anatomy"):
        apply_injury(absent, wound("skull", 1), ht=12, rng=RecordedDice([]), system=True)
    with pytest.raises(ValidationError, match="eyes/vitals"):
        apply_injury(state, wound("vitals", 1), ht=12, rng=RecordedDice([]), system=True)
    assert attack_penalty("left-arm", shield_side="left") == -4
    assert attack_penalty("left-hand", shield_side="left") == -8


async def target(cid: str, play: PlayService, location: HitLocation) -> None:
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
            hit_location=location,
        ),
        authenticated_actor_id="a",
    )


async def test_melee_limb_wound_grip_and_duration_survive_sqlite(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await target(cid, play, "right-arm")
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(
        AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([3, 3, 3, 4, 3, 3, 3])
    )
    result = await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury and result.injury.location == "right-arm"
    assert result.injury.attack.effective_target == 11
    assert result.injury.injury == 6
    state = restarted._load(await restarted.store.read(cid))
    assert not next(i for i in state.resources.items if i.id == "sword-b").ready
    assert next(i for i in state.resources.items if i.id == "shield-b").ready
    with pytest.raises(ValidationError):
        mode(restarted.rules_context, state, "b", "sword-b", "swing")
    restarted.rng = RecordedDice([3, 3, 3])
    end = EndEncounter(
        id="end", actor_id="gm", expected_revision=3, encounter_id="fight", reason="disengaged"
    )
    await CombatService(restarted).execute(cid, end, authenticated_actor_id="gm")
    restarted.rng = RecordedDice([])
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )
    await CombatService(restarted).execute(cid, end, authenticated_actor_id="gm")
    state = restarted._load(await restarted.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury and hp.injury.lasting_injuries[0].duration == "temporary"
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


async def test_missing_anatomy_and_invalid_location_are_rejected_before_dice(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="anatomy"):
        await target(cid, play, "random")
    assert before == await play.store.read(cid)


async def test_leg_and_blindness_affect_real_combat_values(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    state = play._load(await play.store.read(cid))
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id="leg",
            actor_id="b",
            expected_revision=state.resources.revision,
            location="left-leg",
            basic_damage=6,
            resistance=0,
            damage_type="cr",
        ),
        ht=20,
        rng=RecordedDice([1, 1, 1]),
        system=True,
    )
    injured = state.model_copy(update={"resources": resources})
    assert movement(play.rules_context, injured, "b") == 0
    participant = state.encounters[0].participants[1]
    assert defense_value(play.rules_context, injured, participant, "block")[0] is not None


def test_timed_shoulder_has_no_hp_cost_and_replays() -> None:
    command = DisableLocation(
        id="shoulder",
        actor_id="a",
        expected_revision=0,
        location="right-arm",
        duration_seconds=1800,
    )
    state, result = apply_location_effect(human(), command, system=True)
    assert state.pools[0].current == 10 and not result.dropped_ready_items
    assert state.pools[0].injury and state.pools[0].injury.lasting_injuries[0].recovery_at == 1800
    assert apply_location_effect(state, command, system=True) == (state, result)


@pytest.mark.parametrize(
    "table,damage,injury,dr,location",
    [
        ([1, 1, 1], 7, 28, 0, "skull"),
        ([1, 1, 2], 2, 4, 1, "skull"),
        ([1, 2, 2], 2, 4, 1, "skull"),
        ([2, 2, 2], 2, 8, 0, "right-eye"),
        ([2, 2, 3], 2, 8, 0, "right-eye"),
        ([3, 3, 3], 2, 0, 2, "skull"),
        ([5, 5, 5], 7, 20, 2, "skull"),
        ([5, 5, 6], 4, 8, 2, "skull"),
        ([5, 6, 6], 2, 4, 1, "skull"),
        ([6, 6, 6], 6, 16, 2, "skull"),
    ],
)
async def test_critical_head_numeric_table(
    tmp_path: Path, table: list[int], damage: int, injury: int, dr: int, location: HumanLocation
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await target(cid, play, "skull")
    play.rng = RecordedDice([1, 1, 1] + table + [1, 1, 1] * 10)
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury
    assert result.injury.critical_table == tuple(table)
    assert result.injury.basic_damage == damage
    assert result.injury.injury == injury and result.injury.resistance == dr
    assert result.injury.location == location


async def test_random_location_and_shield_arm_effects_are_persisted(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await target(cid, play, "random")
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4, 4, 1, 1, 1])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury and result.injury.location == "left-arm"
    assert result.injury.location_dice == (4, 4, 4)
    assert result.injury.attack.effective_target == 13
    state = play._load(await play.store.read(cid))
    assert next(i for i in state.resources.items if i.id == "shield-b").ready
    participant = state.encounters[0].participants[1]
    with pytest.raises(ValidationError, match="No available"):
        defense_value(play.rules_context, state, participant, "block")
    value, _ = defense_value(play.rules_context, state, participant, "dodge")
    assert value and value.value == 8  # Basic Dodge 8; shield's DB1 is reduced to zero.
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == result


async def test_critical_head_forces_exactly_one_do_nothing_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await target(cid, play, "skull")
    play.rng = RecordedDice([1, 1, 1, 2, 3, 3, 1])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury and result.injury.injury == 0 and not result.injury.adjudication_required
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[1].forced_do_nothing
    assert defense_value(play.rules_context, state, state.encounters[0].participants[1], "block")[0]
    play.rng = RecordedDice([])
    forced = await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="forced",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-b",
            mode_id="swing",
            target_id="a",
        ),
        authenticated_actor_id="b",
    )
    assert forced.code == "combat.do_nothing"
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert not state.encounters[0].participants[1].forced_do_nothing
    assert state.encounters[0].participants[1].last_maneuver == "do_nothing"


async def test_critical_head_scar_is_applied_without_adjudication_pause(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await target(cid, play, "face")
    play.rng = RecordedDice([1, 1, 1, 4, 4, 4] + [1, 1, 1] * 10)
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury and result.injury.critical_table == (4, 4, 4)
    assert result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury
    scars = tuple(i for i in hp.injury.lasting_injuries if i.kind == "scarred")
    assert len(scars) == 1 and scars[0].duration == "permanent"
    assert appearance_levels_lost(hp.injury.lasting_injuries, now=0, full_hp=True) == 1


@pytest.mark.parametrize("dx_dice,retained", [([3, 3, 3], True), ([5, 5, 5], False)])
def test_two_handed_grip_retention_is_recorded(dx_dice: list[int], retained: bool) -> None:
    state = human().model_copy(
        update={
            "owners": (Owner(actor_id="a", capacity=100),),
            "items": (
                Item(id="weapon", definition_id="fixture", owner_id="a", ready=True, equipped=True),
            ),
        }
    )
    state, result = apply_injury(
        state,
        wound("left-hand", 3),
        ht=12,
        dx=12,
        rng=RecordedDice(dx_dice + [3, 3, 3]),
        system=True,
        held_item_ids=("weapon",),
        held_item_locations=(("weapon", "left-hand"), ("weapon", "right-hand")),
    )
    assert state.items[0].ready == retained
    assert result.checks[0].reason == "grip-retention"
    assert result.checks[0].check.dice == tuple(dx_dice)
