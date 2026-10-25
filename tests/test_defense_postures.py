"""B549/B551 actual defenses use every posture without changing attack math."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_basic_combat import start_basic
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_retreat_postures import Representation, battlefield
from test_unarmed import action

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.combat import Posture, posture_defense_modifier
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import DEFENSE_POSTURE_KEY, replay_payload

DefenseKind = Literal["dodge", "block", "weapon-parry", "bare-parry"]
POSTURES: tuple[Posture, ...] = (
    "standing",
    "crouching",
    "kneeling",
    "sitting",
    "crawling",
    "prone",
)


async def pending(
    path: Path,
    backend: str,
    posture: Posture,
    kind: DefenseKind,
    *,
    representation: Representation = "square",
) -> tuple[str, PlayService, ChooseDefense]:
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=kind == "bare-parry",
        start_encounter=representation != "basic",
        scene_bound=True,
        aware_of=("a", "b"),
        runtime_world=world().learn("a", "promise"),
        battlefield=battlefield() if representation == "hex" else None,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        )
        if representation == "hex"
        else None,
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    if representation == "basic":
        await CombatService(play).execute(cid, start_basic(1), principal_id="gm")
    if posture != "standing":
        await turn(cid, play, "a", "do_nothing")
        if posture == "crouching":
            await turn(cid, play, "b", "do_nothing", crouch="before")
        else:
            await turn(cid, play, "b", "change_posture", posture=posture)
    play.rng = RecordedDice([3, 3, 3])
    if kind == "bare-parry":
        # Actual ordinary punches permit an untrained DX-based barehand Parry.
        await action(cid, play, "a", "punch", target="b", hands=("left-hand",), enter=True)
    else:
        await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[1].posture == posture
    return (
        cid,
        play,
        ChooseDefense(
            id="posture-defense",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge" if kind == "dodge" else "block" if kind == "block" else "parry",
            item_id="left-hand"
            if kind == "bare-parry"
            else "sword-b"
            if kind == "weapon-parry"
            else None,
        ),
    )


@pytest.mark.parametrize(
    "posture,modifier",
    [
        ("standing", 0),
        ("crouching", 0),
        ("kneeling", -2),
        ("sitting", -2),
        ("crawling", -3),
        ("prone", -3),
    ],
)
def test_selected_source_posture_table_is_independent_of_legacy_omissions(
    posture: Posture, modifier: int
) -> None:
    assert posture_defense_modifier(posture) == modifier
    assert posture_defense_modifier(posture, legacy=True) == (
        -3 if posture == "prone" else -2 if posture == "kneeling" else 0
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture", POSTURES)
@pytest.mark.parametrize(
    "kind,base", [("dodge", 9), ("block", 10), ("weapon-parry", 10), ("bare-parry", 8)]
)
async def test_actual_square_defense_and_injury_include_each_source_posture_once(
    tmp_path: Path, backend: str, posture: Posture, kind: DefenseKind, base: int
) -> None:
    cid, play, command = await pending(tmp_path, backend, posture, kind)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    expected = (
        base
        + {
            "standing": 0,
            "crouching": 0,
            "kneeling": -2,
            "sitting": -2,
            "crawling": -3,
            "prone": -3,
        }[posture]
    )
    hit = expected < 6
    play.rng = RecordedDice(
        [3, 3, 3, 2, 2, 2] + ([5 if kind == "bare-parry" else 3] if hit else [])
    )
    result = await CombatService(play).execute(cid, command, principal_id="b")
    if kind == "bare-parry":
        assert result.unarmed and len(result.unarmed.checks) == 2
        defense = result.unarmed.checks[1]
        damage = result.unarmed.injury
    else:
        assert result.injury and result.injury.defense
        defense = result.injury.defense
        damage = result.injury.injury
    assert defense.effective_target == expected
    assert defense.outcome.succeeded == (not hit)
    # This Broadsword thrust is1d−1 crushing: die3 yields2HP. A punch rolls5
    # against1d−3 crushing for2HP. Neither packet requires a major-wound roll.
    assert damage == (2 if hit else 0)
    final = await play.store.read(cid)
    updated = play._load(final)
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == (8 if hit else 10)
    assert updated.encounters[0].participants[1].posture == posture
    assert play.rng.exhausted()
    raw = (await play.store.history(cid))[-1].command_input
    assert raw
    caller = replay_payload(raw)
    assert isinstance(caller, dict) and DEFENSE_POSTURE_KEY not in caller
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("representation", ["basic", "hex"])
@pytest.mark.parametrize("posture,target", [("sitting", 7), ("crawling", 6)])
async def test_real_basic_and_hex_posture_producers_feed_the_same_actual_dodge(
    tmp_path: Path, backend: str, representation: Representation, posture: Posture, target: int
) -> None:
    cid, play, command = await pending(
        tmp_path, backend, posture, "dodge", representation=representation
    )
    play.rng = RecordedDice([3, 3, 3, 2, 2, 3] + ([3] if target < 7 else []))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == target
    assert result.injury.injury == (2 if target < 7 else 0) and play.rng.exhausted()
