"""Campaigns fourth printing B376, B556-557: armed thrown-Parry consequences.

Handwritten numeric cases; first-printing/errata certification remains #191.
"""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.combat import GridPoint, RangedSituation
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult
from wayfarer.simulation.mechanics.weapon_flight import position, retrieve
from wayfarer.simulation.ranged_critical import RangedCritical


@pytest.mark.parametrize("second", [False, True])
@pytest.mark.parametrize(
    "mode,table,injury",
    [
        ("swing", (1, 2, 2), 6),
        ("swing", (2, 2, 2), 3),
        ("thrust", (1, 2, 2), 3),
        ("thrust", (2, 2, 2), 1),
    ],
)
async def test_selected_thrown_parry_mode_and_retry(
    tmp_path: Path, second: bool, mode: str, table: tuple[int, int, int], injury: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene()
        + (RangedSituation(attacker_id="b", defender_id="a", distance_yards=2),),
    )
    subject, attacker = ("a", "b") if second else ("b", "a")
    if second:
        await turn(cid, play, "a", "all_out_defense", defense_option="double")
    await turn(
        cid,
        play,
        attacker,
        "attack",
        item_id=f"sword-{attacker}",
        target_id=subject,
        mode_id="ranged",
    )
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="selected-ranged-parry",
        actor_id=subject,
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge" if second else "parry",
        item_id=None if second else f"sword-{subject}",
        parry_mode_id=None if second else mode,
        second_defense="parry" if second else None,
        second_item_id=f"sword-{subject}" if second else None,
        second_parry_mode_id=mode if second else None,
    )
    # Skill 13 succeeds on 9. Failed Dodge (if selected), critical Parry,
    # right-arm self-hit with a damage die of 4, then incoming 1d cr for 3.
    play.rng = RecordedDice(
        [3, 3, 3, *([4, 4, 4] if second else []), 6, 6, 6, *table, 1, 1, 4, *([3] * 20)]
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=subject)
    assert result.injury and result.injury.adjudication_required is None
    saved = play._load(await play.store.read(cid))
    limb = CriticalLimbResult.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("critical-limb:"))
    )
    assert limb.resolved and limb.injury == injury and limb.location == "right-arm"
    assert (
        next(p.current for p in saved.resources.pools if p.id == f"hp:{subject}") == 10 - injury - 3
    )
    context = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert context.subject_id == subject
    assert context.affected_item_id == f"sword-{subject}"
    assert context.affected_mode_id == mode
    assert context.table_rolls == (table,)  # A crushing/cutting Parry is not a ranged self-hit.
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=subject)
        == result
    )
    assert restarted._load(await restarted.store.read(cid)).resources == saved.resources
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("table", [(3, 3, 3), (3, 3, 4), (3, 4, 4), (4, 5, 5)])
async def test_ranged_drop_has_authoritative_landing(
    tmp_path: Path, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(),
        ranged_scene=scene(),
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, *table])
    before = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="drop",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    actor = encounter.participants[0]
    dropped = next(i for i in state.resources.items if i.id == "sword-a")
    assert dropped.ground == position(encounter, actor)
    assert not dropped.equipped and not dropped.ready
    # A later movement cannot relocate the item or turn Ready into a remote pickup.
    remote = encounter.model_copy(
        update={
            "participants": (
                actor.model_copy(update={"position": GridPoint(x=2, y=0)}),
                *encounter.participants[1:],
            )
        }
    )
    with pytest.raises(ValidationError, match="recorded location"):
        retrieve(state, remote, "a", "sword-a")
    restored = retrieve(state, encounter, "a", "sword-a")
    assert next(i for i in restored.resources.items if i.id == "sword-a").ground is None
    assert restored.resources.ammunition_loads == state.resources.ammunition_loads
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "ready", item_id="sword-a")
    recovered = play._load(await play.store.read(cid))
    item = next(i for i in recovered.resources.items if i.id == "sword-a")
    assert item.ground is None and item.ready and item.equipped
    assert recovered.resources.ammunition_loads == state.resources.ammunition_loads
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("location,table", [("torso", (4, 4, 4)), ("face", (4, 5, 5))])
async def test_critical_hit_drops_at_defender_position(
    tmp_path: Path, location: str, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(),
        ranged_scene=scene(),
    )
    await load(cid, play)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        hit_location=location,
    )
    play.rng = RecordedDice([1, 1, 1, *table, 1, *([1, 1, 1] if location == "face" else [])])
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    target = state.encounters[0].participants[1]
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.ground == position(state.encounters[0], target)
    assert not item.ready and not item.equipped
    with pytest.raises(ValidationError):
        retrieve(state, state.encounters[0], "a", "sword-b")


@pytest.mark.parametrize(
    "selected,mode,second_mode",
    [
        ("dodge", "swing", None),
        ("parry", "missing", None),
        ("parry", None, "swing"),
    ],
)
async def test_invalid_thrown_parry_modes_reject_before_dice(
    tmp_path: Path,
    selected: str,
    mode: str | None,
    second_mode: str | None,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await defend(
            cid,
            play,
            "b",
            selected,
            item_id="sword-b",
            parry_mode_id=mode,
            second_parry_mode_id=second_mode,
        )
    assert await play.store.read(cid) == before


async def test_unresolved_thrown_parry_retains_selected_mode(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 1, 2, 2])
    result = await defend(cid, play, "b", "parry", item_id="sword-b", parry_mode_id="swing")
    assert result.injury and result.injury.adjudication_required == "ranged-critical-parry"
    saved = play._load(await play.store.read(cid))
    context = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert context.affected_mode_id == "swing" and context.table_rolls == ((1, 2, 2),)
    # Existing persisted v1 contexts remain readable without inferred damage modes.
    legacy = context.model_dump_json(exclude={"affected_mode_id"})
    assert RangedCritical.model_validate_json(legacy).affected_mode_id is None
