"""Expected critical limb outcomes use the canonical combat transaction."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.critical import CriticalMiss
from wayfarer.simulation.gurps_equipment import MeleeMode
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult, resolve_limb
from wayfarer.simulation.mechanics.gurps_melee import mode


@pytest.mark.parametrize("table,injury", [((2, 2, 1), 6), ((2, 2, 2), 3)])
async def test_self_wound_uses_canonical_limb_damage_and_persists(
    tmp_path: Path, table: tuple[int, int, int], injury: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, *table, 1, 1, 4, *([3] * 12)])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical-limb:"))
    effect = CriticalLimbResult.model_validate_json(event.kind)
    assert effect.resolved and effect.location == "right-arm"
    assert effect.location_dice == (1, 1) and effect.damage_dice == (4,)
    assert effect.injury == injury and effect.table_rolls == (table,)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.current == 10 - injury
    assert result.injury is not None and result.injury.adjudication_required is None
    sword = next(i for i in state.resources.items if i.id == "sword-a")
    assert sword.ready is (injury < 6)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )
    assert restarted._load(await restarted.store.read(cid)).resources == state.resources


async def test_shoulder_uses_wielding_arm_without_dropping_weapon(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, 5, 5, 5])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.current == 10 and hp.injury is not None
    lasting = hp.injury.lasting_injuries[0]
    assert lasting.location == "right-arm" and lasting.duration == "timed"
    assert lasting.recovery_at == lasting.inflicted_at + 1800
    assert next(i for i in state.resources.items if i.id == "sword-a").ready
    assert result.injury is not None and result.injury.adjudication_required is None
    with pytest.raises(ValidationError, match="crippled"):
        mode(play.rules_context, state, "a", "sword-a", "swing")


async def test_double_defense_second_critical_parry_is_deferred_once(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await turn(cid, play, "a", "all_out_defense", defense_option="double")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 4, 4, 4, 6, 6, 6, 2, 2, 1])
    result = await defend(cid, play, "a", "dodge", second_defense="parry")
    assert result.injury and result.injury.second_defense
    assert result.injury.hp_before == result.injury.hp_after
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical:"))
    context = CriticalMiss.model_validate_json(event.kind)
    assert context.subject_id == "a" and context.item_id == "sword-a"
    assert context.action == "parry" and context.table_total == 5


async def test_shoulder_cancels_remaining_all_out_attack(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        attack_option="double",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
    )
    play.rng = RecordedDice([6, 6, 6, 5, 5, 5])
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.adjudication_required is None
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.pending_defense is None and encounter.current_actor_id == "b"


@pytest.mark.parametrize("second,resolved", [((3, 3, 3), False), ((2, 2, 2), True)])
async def test_impaling_exception_rolls_table_once_then_replays(
    tmp_path: Path, second: tuple[int, int, int], resolved: bool
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    combat = play.engine.rules.combat
    assert combat is not None and combat.gurps_equipment is not None
    equipment = combat.gurps_equipment
    entries = tuple(
        e.model_copy(
            update={
                "modes": tuple(
                    m.model_copy(
                        update={"damage": m.damage.model_copy(update={"damage_type": "imp"})}
                    )
                    if isinstance(m, MeleeMode)
                    else m
                    for m in e.modes
                )
            }
        )
        for e in equipment.entries
    )
    play.engine.rules = play.engine.rules.model_copy(
        update={
            "combat": combat.model_copy(
                update={"gurps_equipment": equipment.model_copy(update={"entries": entries})}
            )
        }
    )
    play.rng = RecordedDice([*second, 1, 1, 5, *([3] * 12)])
    updated, encounter, result = resolve_limb(
        play.rules_context,
        state,
        state.encounters[0],
        table=(2, 2, 1),
        defender_item=None,
        blocker="basic-critical-miss:5",
    )
    assert result.table_rolls == ((2, 2, 1), second) and result.resolved is resolved
    play.rng = RecordedDice([])
    assert resolve_limb(
        play.rules_context,
        updated,
        encounter,
        table=(2, 2, 1),
        defender_item=None,
        blocker="basic-critical-miss:5",
    ) == (updated, encounter, result)
    play.rng = RecordedDice([6, 6, 6, 2, 2, 1, *second, 1, 1, 5, *([3] * 12)])
    receipt = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert receipt.injury and receipt.injury.critical_table == second
    assert receipt.injury.adjudication_required is None
    persisted = play._load(await play.store.read(cid))
    recorded = next(e for e in persisted.resources.events if e.id.startswith("critical-limb:"))
    assert CriticalLimbResult.model_validate_json(recorded.kind).table_rolls == ((2, 2, 1), second)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == receipt
