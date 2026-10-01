"""B374-377/B398-400 source-derived defense and hit-location cases (#835)."""

from pathlib import Path

import pytest
from test_basic_combat import start_basic
from test_gurps_melee import attack, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.combat.encounter import basic_distance
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense
from wayfarer.engine.simulation.combat.vocabulary import Defense, Posture
from wayfarer.engine.simulation.equipment.catalog import Parry
from wayfarer.engine.simulation.health.hit_locations import attack_penalty
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize(("posture", "penalty"), [("standing", 0), ("kneeling", -2), ("prone", -3)])
@pytest.mark.parametrize(
    ("selected", "item", "base"),
    [("dodge", None, 9), ("parry", "sword-b", 10), ("block", "shield-b", 10)],
)
async def test_posture_and_equipment_defense_numbers(
    tmp_path: Path, posture: Posture, penalty: int, selected: Defense, item: str | None, base: int
) -> None:
    # Source formulas: Dodge=floor(Speed)+3, Parry/Block=floor(skill/2)+3;
    # this fixture's ready small shield adds DB1 to each selected defense.
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1].model_copy(update={"posture": posture})
    before = state.model_dump_json()
    play.rng = RecordedDice([])
    score, used = defense_value(
        play.rules_context,
        state,
        defender,
        selected,
        item,
        parry_mode_id="swing" if selected == "parry" else None,
    )
    assert score is not None and score.value == base + penalty
    assert used == item
    assert play.rng.exhausted() and state.model_dump_json() == before


@pytest.mark.parametrize(("fencing", "expected"), [(False, 11), (True, 13)])
async def test_fencing_retreat_adds_three_against_same_foe(
    tmp_path: Path, fencing: bool, expected: int
) -> None:
    # B377: ordinary parry retreat +1; fencing +3, including DB1.
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, parry=Parry(modifier=0, fencing=fencing)
    )
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    play.rng = RecordedDice([])
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    defender = encounter.participants[1].model_copy(
        update={"retreat_used": True, "retreat_attacker_id": "a", "tactical_defense_bonus": 1}
    )
    score, _ = defense_value(
        play.rules_context, state, defender, "parry", "sword-b", parry_mode_id="swing"
    )
    assert score is not None and score.value == expected
    another_foe = defender.model_copy(
        update={"retreat_attacker_id": "other", "tactical_defense_bonus": 0}
    )
    score, _ = defense_value(
        play.rules_context, state, another_foe, "parry", "sword-b", parry_mode_id="swing"
    )
    assert score is not None and score.value == 10
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("location", "penalty"),
    [
        ("torso", 0),
        ("vitals", -3),
        ("skull", -7),
        ("face", -5),
        ("neck", -5),
        ("groin", -3),
        ("right-arm", -2),
        ("left-leg", -2),
        ("right-hand", -4),
        ("left-foot", -4),
        ("left-eye", -9),
    ],
)
def test_hit_location_independent_penalties(location: HumanLocation, penalty: int) -> None:
    # B398-400/B552; selected limbs are distinct, generic anatomy isn't assumed.
    assert attack_penalty(location) == penalty


@pytest.mark.parametrize(
    ("location", "expected"),
    [("left-arm", -4), ("left-hand", -8), ("right-arm", -2), ("right-hand", -4), ("torso", 0)],
)
def test_shield_side_doubles_only_protected_limb_penalty(
    location: HumanLocation, expected: int
) -> None:
    # B400: shield-side arm/hand twice the normal hit-location penalty.
    assert attack_penalty(location, shield_side="left") == expected


async def test_basic_fencing_retreat_transition_retry_and_authority(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        parry=Parry(modifier=0, fencing=True),
        start_encounter=False,
        scene_bound=True,
    )
    service = CombatService(play)
    await service.execute(cid, start_basic(1), principal_id="gm")
    play.rng = RecordedDice([3, 3, 3])
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="fencing-retreat",
        actor_id="b",
        expected_revision=2,
        encounter_id="fight",
        defense="parry",
        item_id="sword-b",
        parry_mode_id="swing",
        basic_retreat=True,
    )
    prepared = prepare_defense(play.rules_context, state, state.encounters[0], command)
    defender = next(actor for actor in prepared.participants if actor.actor_id == "b")
    updated = state.model_copy(update={"encounters": (prepared,)})
    assert basic_distance(prepared, "a", "b") == 2
    assert defender.retreat_used and defender.retreat_attacker_id == "a"
    score, _ = defense_value(
        play.rules_context, updated, defender, "parry", "sword-b", parry_mode_id="swing"
    )
    assert score is not None and score.value == 13
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await service.execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3, 3])
    receipt = await service.execute(cid, command, principal_id="b")
    settled = play._load(await play.store.read(cid))
    assert basic_distance(settled.encounters[0], "a", "b") == 2
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == receipt
    assert await play.store.replay(cid) == await play.store.read(cid)
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid, command.model_copy(update={"id": "stale-retreat"}), principal_id="b"
        )
