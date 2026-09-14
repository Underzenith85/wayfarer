"""Characters B273-B274 and Campaigns B416/B432 cattle-prod behavior."""

from pathlib import Path

from test_gurps_melee import choice, setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import ElectricalStun, InjuryStatus
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.melee.electrical import (
    electrical_armor,
    resolve_cattle_prod,
)
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Armor, EquipmentProfile
from wayfarer.engine.simulation.health.injury import InjuryTurn, apply_injury
from wayfarer.engine.simulation.resources import Item, Pool, ResourceState
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def _injury_state(status: InjuryStatus | None = None) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:b",
                current=10,
                maximum=10,
                injury=status
                or InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human"),
            ),
        )
    )


def _shortsword_definition() -> RuleDefinition:
    return RuleDefinition(
        "skill:shortsword",
        DefinitionKind.SKILL,
        "Shortsword",
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.DX, Difficulty.AVERAGE, "B208/B273"),
    )


def _prod_profile() -> EquipmentProfile:
    return next(
        entry for entry in BASIC_EQUIPMENT.entries if entry.definition_id == "equipment:cattle-prod"
    )


async def _combat(
    tmp_path: Path, command_id: str, dice: list[int]
) -> tuple[str, PlayService, CombatResult]:
    tmp_path.mkdir(parents=True)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=(_shortsword_definition(),),
        extra_purchases=(Purchase(definition_id="skill:shortsword", amount=12),),
        extra_equipment=(_prod_profile(),),
        extra_items=(
            Item(
                id="prod-a",
                definition_id="equipment:cattle-prod",
                owner_id="a",
                equipped=True,
                ready=True,
            ),
        ),
    )
    attack = TakeCombatTurn(
        id=command_id,
        actor_id="a",
        expected_revision=1,
        encounter_id="fight",
        maneuver="attack",
        item_id="prod-a",
        mode_id="shortsword-burn",
        target_id="b",
        electrical_contact_seconds=2,
    )
    await CombatService(play).execute(cid, attack, principal_id="a")
    play.rng = RecordedDice(dice)
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    return cid, play, result


async def test_cattle_prod_hit_damage_affliction_armor_and_miss(tmp_path: Path) -> None:
    metal = Armor(locations=("torso",), dr=7)
    leather = Armor(locations=("torso",), dr=2, electrical_conductivity="nonmetallic")
    insulated = Armor(locations=("torso",), dr=3, electrical_conductivity="insulated")
    assert electrical_armor((metal,)) == (1, 0, False)
    assert electrical_armor((leather,)) == (2, 4, False)
    assert electrical_armor((metal, leather)) == (2, 4, False)
    assert electrical_armor((insulated,)) == (3, 0, True)
    resisted, resistance = resolve_cattle_prod(
        _injury_state(),
        event_id="resisted",
        target_id="b",
        ht=10,
        armor_bonus=4,
        insulated=False,
        contact_seconds=0,
        rng=RecordedDice([3, 3, 3]),
    )
    resisted_hp = next(pool for pool in resisted.pools if pool.id == "hp:b")
    assert resistance.outcome == "resisted"
    assert resistance.resistance is not None and resistance.resistance.effective_target == 11
    assert resisted_hp.injury is not None and not resisted_hp.injury.stunned

    unaffected, insulation = resolve_cattle_prod(
        _injury_state(),
        event_id="insulated",
        target_id="b",
        ht=10,
        armor_bonus=0,
        insulated=True,
        contact_seconds=0,
        rng=RecordedDice([]),
    )
    assert insulation.outcome == "unaffected" and insulation.resistance is None
    assert unaffected.pools[0].injury is not None and not unaffected.pools[0].injury.stunned

    cid, hit_play, hit = await _combat(tmp_path / "hit", "prod-hit", [3, 3, 3, 3, 4, 4, 4])
    assert hit.injury is not None
    assert hit.injury.basic_damage == 1 and hit.injury.injury == 1
    assert hit.injury.effect_dice == (4, 4, 4)
    hit_state = hit_play._load(await hit_play.store.read(cid))
    hit_hp = next(pool for pool in hit_state.resources.pools if pool.id == "hp:b")
    assert hit_hp.injury is not None and hit_hp.injury.stunned

    miss_cid, miss_play, miss = await _combat(tmp_path / "miss", "prod-miss", [5, 5, 5])
    assert miss.injury is not None and miss.injury.injury == 0
    assert miss.injury.effect_dice == ()
    miss_state = miss_play._load(await miss_play.store.read(miss_cid))
    miss_hp = next(pool for pool in miss_state.resources.pools if pool.id == "hp:b")
    assert miss_hp.injury is not None and not miss_hp.injury.stunned


async def test_cattle_prod_contact_recovery_retry_and_replay(tmp_path: Path) -> None:
    failed, result = resolve_cattle_prod(
        _injury_state(),
        event_id="linked",
        target_id="b",
        ht=10,
        armor_bonus=0,
        insulated=False,
        contact_seconds=4,
        rng=RecordedDice([4, 4, 4]),
    )
    assert result.outcome == "stunned" and result.recovery_starts_turn == 14
    assert resolve_cattle_prod(
        failed,
        event_id="linked",
        target_id="b",
        ht=10,
        armor_bonus=0,
        insulated=False,
        contact_seconds=4,
        rng=RecordedDice([]),
    ) == (failed, result)

    electrical = ElectricalStun(
        source_id="equipment:cattle-prod",
        contact_ends_turn=4,
        recovery_starts_turn=14,
    )
    acting = _injury_state(
        InjuryStatus(
            profile_id="gurps-basic-set-4e-2004",
            anatomy="human",
            stunned=True,
            electrical_stun=electrical,
            turn=13,
            phase="acting",
        )
    )
    waiting, wait_result = apply_injury(
        acting,
        InjuryTurn(
            id="wait", actor_id="b", expected_revision=0, turn=13, phase="end", do_nothing=True
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert not wait_result.checks
    waiting_hp = next(pool for pool in waiting.pools if pool.id == "hp:b")
    assert waiting_hp.injury is not None and waiting_hp.injury.stunned

    started, _ = apply_injury(
        waiting,
        InjuryTurn(
            id="start", actor_id="b", expected_revision=1, turn=14, phase="start", do_nothing=True
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    recovered, recovery = apply_injury(
        started,
        InjuryTurn(
            id="recover", actor_id="b", expected_revision=2, turn=14, phase="end", do_nothing=True
        ),
        ht=10,
        rng=RecordedDice([2, 2, 3]),
        system=True,
    )
    assert recovery.checks[0].check.effective_target == 7
    recovered_hp = next(pool for pool in recovered.pools if pool.id == "hp:b")
    assert recovered_hp.injury is not None
    assert not recovered_hp.injury.stunned and recovered_hp.injury.electrical_stun is None

    cid, play, combat = await _combat(tmp_path / "replay", "prod-replay", [3, 3, 3, 3, 4, 4, 4])
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), principal_id="b") == combat
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await restarted.store.replay(cid) == await play.store.read(cid)
