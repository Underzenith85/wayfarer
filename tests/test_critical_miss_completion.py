"""B556-557 expected consequences, committed once through CombatService."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import turn
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.critical import CriticalMiss
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult
from wayfarer.simulation.mechanics.weapon_flight import FlightResult, resolve_flight


async def restart(play: PlayService) -> PlayService:
    assert isinstance(play.store, AsyncSQLiteStore)
    return PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))


@pytest.mark.parametrize("second", [False, True])
@pytest.mark.parametrize(
    "table,expected,selected_mode",
    [
        ((2, 2, 1), 6, "swing"),
        ((2, 2, 2), 3, "swing"),
        ((5, 5, 5), 0, "swing"),
        ((2, 2, 1), 3, "thrust"),
        ((2, 2, 2), 1, "thrust"),
    ],
)
async def test_selected_parry_mode_resolves_limb_and_incoming_hit_once(
    tmp_path: Path, second: bool, table: tuple[int, int, int], expected: int, selected_mode: str
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    if second:
        await turn(cid, play, "a", "all_out_defense", defense_option="double")
        await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
        subject = "a"
    else:
        await attack(cid, play)
        subject = "b"
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="selected-parry",
        actor_id=subject,
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge" if second else "parry",
        item_id=None if second else f"sword-{subject}",
        parry_mode_id=None if second else selected_mode,
        second_defense="parry" if second else None,
        second_item_id=f"sword-{subject}" if second else None,
        second_parry_mode_id=selected_mode if second else None,
    )
    # Ordinary incoming hit; critical parry. Swing 4+1 cut -> 7 injury,
    # capped to 6 on an arm; row 6 halves basic damage first -> 3 injury.
    # This fixture's thrust is crushing: 4-2+1 -> 3, or half -> 1.
    limb_dice = [] if sum(table) == 15 else [1, 1, 4, *([3] * 12)]
    play.rng = RecordedDice(
        [4, 4, 4, *([4, 4, 4] if second else []), 6, 6, 6, *table, *limb_dice, *([3] * 12)]
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=subject)
    assert result.injury and result.injury.adjudication_required is None
    saved = play._load(await play.store.read(cid))
    event = next(e for e in saved.resources.events if e.id.startswith("critical-limb:"))
    limb = CriticalLimbResult.model_validate_json(event.kind)
    assert event.target_id == subject and limb.resolved and limb.injury == expected
    assert limb.location == "right-arm"
    hp = next(p for p in saved.resources.pools if p.id == f"hp:{subject}")
    # The failed parry also admits the incoming attack: (3+1)*1.5 = 6.
    assert hp.current == 10 - expected - 6
    if sum(table) == 15:
        assert hp.injury is not None
        strain = next(i for i in hp.injury.lasting_injuries if i.location == "right-arm")
        assert strain.recovery_at == strain.inflicted_at + 1800
        assert next(i for i in saved.resources.items if i.id == f"sword-{subject}").ready
    restarted = await restart(play)
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=subject)
        == result
    )
    assert restarted._load(await restarted.store.read(cid)).resources == saved.resources


async def test_selected_mode_is_preserved_when_anatomy_is_missing(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    command = choice("parry").model_copy(update={"parry_mode_id": "swing"})
    play.rng = RecordedDice([4, 4, 4, 6, 6, 6, 2, 2, 1])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury and result.injury.adjudication_required == "basic-critical-miss:5:defender"
    saved = play._load(await play.store.read(cid))
    context = CriticalMiss.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("critical:"))
    )
    assert tuple(w.mode.id for w in context.weapons) == ("swing",)
    assert context.incoming and context.incoming.actor_id == "b"
    restarted = await restart(play)
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )


@pytest.mark.parametrize(
    "defense,mode,second",
    [("dodge", "swing", None), ("parry", "missing", None), ("parry", None, "swing")],
)
async def test_invalid_parry_mode_rejected_before_dice_or_state_change(
    tmp_path: Path, defense: str, mode: str | None, second: str | None
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    command = choice(defense).model_copy(
        update={"parry_mode_id": mode, "second_parry_mode_id": second}
    )
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize(
    "direction,dx_roll,injury", [(1, (6, 6, 6), 3), (1, (1, 1, 1), 0), (6, (), 0)]
)
async def test_flight_geometry_collision_and_restart(
    tmp_path: Path, direction: int, dx_roll: tuple[int, ...], injury: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    pending = play._load(await play.store.read(cid)).encounters[0]
    play.rng = RecordedDice([6, 6, 6, 4, 5, 5, 1, direction, *dx_roll, *([4] if injury else [])])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical-flight:"))
    flight = FlightResult.model_validate_json(event.kind)
    assert (flight.landing.x, flight.landing.y) == ((1, 0) if direction == 1 else (-1, 0))
    assert sum(c.injury for c in flight.collisions) == injury
    assert len(flight.collisions) == (1 if direction == 1 else 0)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - injury
    restarted = await restart(play)
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )
    reloaded = restarted._load(await restarted.store.read(cid))
    assert reloaded.resources == state.resources
    # Direct consequence retry must not change an already recorded table.
    assert resolve_flight(restarted.rules_context, reloaded, pending, (4, 5, 5))[0] == reloaded
    with pytest.raises(ConflictError, match="cannot be replaced"):
        resolve_flight(restarted.rules_context, reloaded, pending, (5, 4, 5))
