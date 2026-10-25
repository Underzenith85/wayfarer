"""Selected Campaigns B377: sitting/kneeling refuse, lying may roll away."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_basic_combat import start_basic
from test_gurps_maneuvers import turn
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.combat import retreat_posture_allowed
from wayfarer.engine.simulation.combat.encounter import basic_distance
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.tactical import occupants
from wayfarer.engine.simulation.combat.tactical_transitions import _hex_retreat_feasible
from wayfarer.engine.simulation.hex_geometry import (
    Cell,
    Hex,
    HexBattlefield,
    Pose,
    RetreatContext,
    can_retreat,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.hex_choices import choices

FEATURE = "retreat-postures"
Representation = Literal["basic", "hex", "square"]
Posture = Literal["standing", "kneeling", "sitting", "prone"]


def battlefield() -> HexBattlefield:
    return HexBattlefield(
        id="field",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(-1, 4) for r in range(-1, 2)),
    )


async def pending(
    path: Path,
    backend: str,
    representation: Representation,
    posture: Posture,
    *,
    attack_pending: bool = True,
) -> tuple[str, PlayService, ChooseDefense]:
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
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
        await turn(cid, play, "b", "change_posture", posture=posture)
    if attack_pending:
        play.rng = RecordedDice([3, 3, 3])
        await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="swing", target_id="b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[1].posture == posture
    return (
        cid,
        play,
        ChooseDefense(
            id="posture-retreat",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
            basic_retreat=representation == "basic",
            retreat=Hex(q=2, r=0) if representation != "basic" else None,
        ),
    )


@pytest.mark.parametrize("posture", ["standing", "kneeling", "sitting", "prone"])
def test_source_postures_and_exact_legacy_geometry_remain_distinct(posture: Posture) -> None:
    source = posture in ("standing", "prone")
    assert retreat_posture_allowed(posture) == source
    assert retreat_posture_allowed(posture, legacy="basic") == (posture != "kneeling")
    assert retreat_posture_allowed(posture, legacy="hex") == (posture not in ("sitting", "prone"))
    pose = Pose.model_validate(
        {
            "position": Hex(q=1, r=0),
            "facing": 3,
            "posture": "lying" if posture == "prone" else posture,
        }
    )
    assert (
        can_retreat(battlefield(), pose, Hex(q=0, r=0), Hex(q=2, r=0), context=RetreatContext())
        == source
    )
    assert can_retreat(
        battlefield(),
        pose,
        Hex(q=0, r=0),
        Hex(q=2, r=0),
        context=RetreatContext(),
        correct_postures=False,
    ) == (posture not in ("sitting", "prone"))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("representation", ["basic", "hex"])
@pytest.mark.parametrize("posture", ["standing", "kneeling", "sitting", "prone"])
async def test_actual_posture_controls_admission_without_changing_defense_penalties(
    tmp_path: Path, backend: str, representation: Representation, posture: Posture
) -> None:
    cid, play, command = await pending(tmp_path, backend, representation, posture)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    play.rng = RecordedDice([])
    offered = choices(play, state, state.encounters[0], "b", frozenset({"a", "b"}))
    assert any(
        isinstance(option.command, ChooseDefense)
        and (option.command.basic_retreat or option.command.retreat is not None)
        for option in offered
    ) == (posture in ("standing", "prone"))
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()
    if representation == "hex":
        encounter = state.encounters[0]
        subject, attacker = encounter.participants[1], encounter.participants[0]
        with combat_generation(frozenset({FEATURE})):
            assert _hex_retreat_feasible(
                state, encounter, subject, attacker, battlefield(), occupants(encounter)
            ) == (posture in ("standing", "prone"))
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"id": "stale", "expected_revision": state.revision - 1}),
            principal_id="b",
        )
    if posture in ("kneeling", "sitting"):
        with pytest.raises(ValidationError, match="Retreat is unavailable"):
            await CombatService(play).execute(cid, command, principal_id="b")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    play.rng = RecordedDice([3, 3, 3])
    with pytest.raises(ValidationError, match="dice"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    # Ground Dodge 9, prone −3, retreat +3: the posture penalty is retained.
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == (9 if posture == "prone" else 12)
    assert result.injury.injury == 0 and play.rng.exhausted()
    final = await play.store.read(cid)
    updated = play._load(final)
    assert updated.encounters[0].participants[1].posture == posture
    if representation == "hex":
        assert updated.encounters[0].participants[1].position == Hex(q=2, r=0)
    else:
        assert basic_distance(updated.encounters[0], "a", "b") == 2
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("representation", ["basic", "hex"])
async def test_prone_rolling_retreat_failure_still_moves_and_applies_actual_injury(
    tmp_path: Path, backend: str, representation: Representation
) -> None:
    cid, play, command = await pending(tmp_path, backend, representation, "prone")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 4, 1])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert (
        result.injury.defense.effective_target == 9 and not result.injury.defense.outcome.succeeded
    )
    assert result.injury.injury == 3 and result.injury.hp_after == 7
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[1].posture == "prone" and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_square_retreat_keeps_unsupported_geometry_refusal(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await pending(tmp_path, backend, "square", "prone")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(
        ValidationError, match="Retreat requires the matching spatial representation"
    ):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()
