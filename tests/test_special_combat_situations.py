"""Independent Campaigns fourth-printing B393-B394 acceptance cases for #509."""

from pathlib import Path

import pytest
from test_basic_combat import opening_facts
from test_gurps_melee import setup as gurps_setup
from test_gurps_recovery import PROFILE

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.tables.tactical import (
    high_speed_turning_radius,
    personal_high_speed_velocity,
)
from wayfarer.engine.rules.types.tactical import HighSpeedState
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    BasicSpatialFact,
    HexActorPlacement,
    HexSpatialContext,
    VisibilitySpatialFact,
)
from wayfarer.engine.simulation.combat.tactical import move_hex
from wayfarer.engine.simulation.combat.visibility import combat_visibility, visible_actors
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.surprise import SurpriseCommand, SurpriseService, SurpriseSides


def h(q: int, r: int = 0) -> Hex:
    return Hex(q=q, r=r)


def board() -> HexBattlefield:
    return HexBattlefield(
        id="speed-course",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(Cell(position=h(q, r)) for q in range(0, 15) for r in range(0, 8)),
    )


def speed_encounter(*, state: HighSpeedState | None = None) -> Encounter:
    actor = Combatant(
        actor_id="runner",
        initiative=10,
        position=h(0),
        hex_facing=0,
        reach=1,
        movement_allowance=5,
        high_speed=state,
    )
    return Encounter(
        id="run",
        spatial_context=HexSpatialContext(
            battlefield_id="speed-course",
            placements=(HexActorPlacement(actor_id="runner", position=h(0), facing=0),),
        ),
        participants=(actor,),
        turn_order=("runner",),
    )


def test_b394_personal_high_speed_enters_and_persists_direction_budget() -> None:
    assert personal_high_speed_velocity(5) == 6
    assert personal_high_speed_velocity(2) == 3
    assert high_speed_turning_radius(11, 5) == 2
    encounter = speed_encounter()
    entered = move_hex(
        encounter,
        encounter.participants[0],
        "move",
        tuple(h(q) for q in range(1, 6)),
        None,
        None,
        board=board(),
        enter_high_speed=True,
    )
    assert entered.position == h(5)
    assert entered.high_speed == HighSpeedState(velocity=6, straight_yards=0, direction=0)

    continued_encounter = speed_encounter(state=entered.high_speed)
    continued = move_hex(
        continued_encounter,
        continued_encounter.participants[0],
        "move_and_attack",
        tuple(h(q) for q in range(1, 7)),
        None,
        None,
        board=board(),
    )
    assert continued.high_speed == HighSpeedState(velocity=6, straight_yards=6, direction=0)


def test_b394_high_speed_rejects_early_turn_and_invalid_path_atomically() -> None:
    encounter = speed_encounter(state=HighSpeedState(velocity=6, direction=0))
    original = encounter.model_dump_json()
    with pytest.raises(ValidationError, match="turning radius"):
        move_hex(
            encounter,
            encounter.participants[0],
            "move",
            (h(0, 1), h(0, 2), h(0, 3), h(0, 4), h(0, 5), h(0, 6)),
            None,
            None,
            board=board(),
        )
    assert encounter.model_dump_json() == original

    with pytest.raises(ValidationError, match="saved velocity"):
        move_hex(
            encounter,
            encounter.participants[0],
            "move",
            (h(1), h(2), h(3), h(4), h(5)),
            None,
            None,
            board=board(),
        )
    assert encounter.model_dump_json() == original

    blocker = Combatant(
        actor_id="blocker",
        initiative=9,
        position=h(6),
        hex_facing=3,
        reach=1,
        movement_allowance=5,
    )
    spatial = encounter.spatial
    assert isinstance(spatial, HexSpatialContext)
    blocked = encounter.model_copy(
        update={
            "participants": encounter.participants + (blocker,),
            "turn_order": ("runner", "blocker"),
            "spatial_context": spatial.model_copy(
                update={
                    "placements": spatial.placements
                    + (HexActorPlacement(actor_id="blocker", position=h(6), facing=3),)
                }
            ),
        }
    )
    blocked_original = blocked.model_dump_json()
    with pytest.raises(ValidationError, match="occupied position"):
        move_hex(
            blocked,
            blocked.participants[0],
            "move",
            tuple(h(q) for q in range(1, 7)),
            None,
            None,
            board=board(),
        )
    assert blocked.model_dump_json() == blocked_original


async def test_b393_surprise_persists_side_initiative_and_replays(tmp_path: Path) -> None:
    cid, play = await gurps_setup(tmp_path, PROFILE)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((5, 2))
    command = SurpriseCommand(
        id="opening-surprise",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        trigger_id="authored-ambush",
    )
    service = SurpriseService(
        play,
        lambda *_: SurpriseSides(("a",), ("b",), "a", "b", total=False),
    )
    await service.execute(cid, command, principal_id="gm")
    first = await play.store.read(cid)
    encounter = play._load(first).encounters[0]
    assert encounter.surprise is not None
    assert encounter.surprise.trigger_id == "authored-ambush"
    assert encounter.surprise.initiative_winner == 0
    assert tuple(side.roll for side in encounter.surprise.sides) == (5, 2)
    assert encounter.turn_order == ("a", "b")
    await service.execute(cid, command, principal_id="gm")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == first


def hidden_facts(*, defender_knows_location: bool) -> tuple[BasicSpatialFact, ...]:
    facts = list(opening_facts(1))
    forward = facts[3]
    reverse = facts[4]
    assert isinstance(forward, VisibilitySpatialFact)
    assert isinstance(reverse, VisibilitySpatialFact)
    facts[3] = forward.model_copy(
        update={"visible": False, "obscuration": "invisible", "location_known": True}
    )
    facts[4] = reverse.model_copy(
        update={
            "visible": False,
            "obscuration": "invisible",
            "location_known": defender_knows_location,
            "aware_of_attack": True,
        }
    )
    return tuple(facts)


async def gurps_basic(
    tmp_path: Path, facts: tuple[BasicSpatialFact, ...]
) -> tuple[str, PlayService]:
    cid, play = await gurps_setup(tmp_path, PROFILE, human=True, aware_of=("a", "b"))
    initial = play._load(await play.store.read(cid))

    def convert(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        encounter = state.encounters[0]
        participants = tuple(
            actor.model_copy(update={"position": None, "hex_facing": None})
            for actor in encounter.participants
        )
        encounter = encounter.model_copy(
            update={
                "spatial_context": BasicSpatialContext(facts=facts),
                "participants": participants,
            }
        )
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "resources": state.resources.model_copy(update={"revision": state.revision + 1}),
                "encounters": (encounter,),
            }
        )
        campaign["revision"], campaign["play_json"] = updated.revision, updated.model_dump_json()
        return CommandReceipt(action="combat", outcome="basic-fixture")

    await play.store.commit_turn(cid, "basic-fixture", initial.revision, "basic-fixture", convert)
    return cid, play.for_campaign(await play.store.read(cid))


async def test_b394_hidden_target_modifiers_are_authoritative_and_not_projected(
    tmp_path: Path,
) -> None:
    cid, play = await gurps_basic(tmp_path, hidden_facts(defender_knows_location=False))
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert visible_actors(state, encounter, "a") == frozenset({"a"})
    assert combat_visibility(encounter, "a", "b").model_dump() == {
        "attack_penalty": -6,
        "defense_penalty": -4,
        "defenses": ("dodge",),
    }
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack-hidden-location",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
            mode_id="swing",
        ),
        principal_id="a",
    )
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None
    assert pending.visibility_attack_penalty == -6
    assert pending.visibility_defense_penalty == -4
    assert pending.allowed == ("none", "dodge")


async def test_b394_unknown_hidden_location_rejects_without_revealing_target(
    tmp_path: Path,
) -> None:
    facts = list(hidden_facts(defender_knows_location=True))
    forward = facts[3]
    assert isinstance(forward, VisibilitySpatialFact)
    facts[3] = forward.model_copy(update={"location_known": False})
    cid, play = await gurps_basic(tmp_path, tuple(facts))
    state = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="Target is unavailable"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="guessed-hidden-target",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                target_id="b",
                item_id="sword-a",
                mode_id="swing",
            ),
            principal_id="a",
        )
    assert play._load(await play.store.read(cid)).revision == state.revision
