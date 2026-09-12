"""Mapless combat examples from Campaigns B367-368 and B377."""

from pathlib import Path
from typing import Literal

import pytest
from test_encounter_context import load, setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    BasicMove,
    ChooseDefense,
    CombatService,
    DeclareBasicSpatialFacts,
    StartBasicEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Intent
from wayfarer.simulation.combat import (
    BasicSpatialContext,
    BasicSpatialFact,
    CoverSpatialFact,
    DistanceSpatialFact,
    ObstacleSpatialFact,
    RangedSituation,
    ReachSpatialFact,
    RetreatSpatialFact,
    SpatialProvenance,
    VisibilitySpatialFact,
    basic_distance,
)
from wayfarer.simulation.mechanics.gurps_ranged import situation


def provenance(
    revision: int,
    *,
    source: Literal["scenario", "gm-adjudication", "engine-derived"] = "scenario",
    source_id: str = "dock-scene",
    declared_by: str = "gm",
) -> SpatialProvenance:
    return SpatialProvenance(
        source=source,
        source_id=source_id,
        declared_by=declared_by,
        declared_revision=revision,
    )


def opening_facts(distance: float, *, retreat: bool = True) -> tuple[BasicSpatialFact, ...]:
    origin = provenance(0)
    relation: Literal["reachable", "separated"] = "reachable" if distance <= 1 else "separated"
    return (
        DistanceSpatialFact(subject_id="a", object_id="b", yards=distance, provenance=origin),
        ReachSpatialFact(subject_id="a", object_id="b", relation=relation, provenance=origin),
        ReachSpatialFact(subject_id="b", object_id="a", relation=relation, provenance=origin),
        VisibilitySpatialFact(subject_id="a", object_id="b", visible=True, provenance=origin),
        VisibilitySpatialFact(subject_id="b", object_id="a", visible=True, provenance=origin),
        CoverSpatialFact(subject_id="a", object_id="b", cover="none", provenance=origin),
        ObstacleSpatialFact(subject_id="a", object_id="b", blocked=False, provenance=origin),
        RetreatSpatialFact(subject_id="b", object_id="a", feasible=retreat, provenance=origin),
    )


def start_basic(
    distance: float, *, retreat: bool = True, ranged: bool = False
) -> StartBasicEncounter:
    return StartBasicEncounter(
        id="start-basic",
        actor_id="gm",
        expected_revision=0,
        encounter_id="fight",
        scene_id="dock-scene",
        participant_ids=("a", "b"),
        facts=opening_facts(distance, retreat=retreat),
        ranged_situations=(
            (
                RangedSituation(
                    attacker_id="a",
                    defender_id="b",
                    speed_yards_per_second=0,
                    size_modifier=0,
                ),
            )
            if ranged
            else ()
        ),
    )


async def test_mapless_approach_step_reach_restart_and_retry(tmp_path: Path) -> None:
    """B367-368: basic movement needs no board; Move 3 and a one-yard step are exact."""
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    await service.execute(cid, start_basic(5, ranged=True), authenticated_actor_id="gm")
    state = await load(play, cid)
    encounter = state.encounters[0]
    assert isinstance(encounter.spatial, BasicSpatialContext)
    encoded = encounter.model_dump(mode="json")
    assert "battlefield_id" not in encoded
    assert all("position" not in actor for actor in encoded["participants"])
    assert encounter.ranged_situations[0].distance_yards is None
    assert situation(play.rules_context, encounter, "a", "b").distance == 5
    projection = await CampaignAccess(play).read(cid, principal_id="alice")
    projected_encounters = projection["encounters"]
    assert isinstance(projected_encounters, tuple)
    projected_encounter = projected_encounters[0]
    assert isinstance(projected_encounter, dict)
    assert projected_encounter["id"] == "fight"

    moved = TakeCombatTurn(
        id="approach",
        actor_id="a",
        expected_revision=1,
        encounter_id="fight",
        maneuver="move",
        basic_move=BasicMove(reference_actor_id="b", direction="approach"),
    )
    await service.execute(cid, moved, authenticated_actor_id="a")
    state = await load(play, cid)
    assert basic_distance(state.encounters[0], "a", "b") == 2
    assert situation(play.rules_context, state.encounters[0], "a", "b").distance == 2
    spatial = state.encounters[0].spatial
    assert isinstance(spatial, BasicSpatialContext)
    assert spatial.active("visibility", "a", "b") is None

    restarted = CombatService(PlayService(play.store, play.engine))
    assert await restarted.execute(cid, moved, authenticated_actor_id="a") == await service.execute(
        cid, moved, authenticated_actor_id="a"
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="hold",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="b",
    )
    with pytest.raises(ValidationError, match="visibility"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="stale-attack",
                actor_id="a",
                expected_revision=3,
                encounter_id="fight",
                maneuver="attack",
                target_id="b",
                item_id="sword-a",
            ),
            authenticated_actor_id="a",
        )
    decision = provenance(3, source="gm-adjudication", source_id="refresh-step")
    await service.execute(
        cid,
        DeclareBasicSpatialFacts(
            id="refresh-step",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fight",
            facts=(
                VisibilitySpatialFact(
                    subject_id="a", object_id="b", visible=True, provenance=decision
                ),
                CoverSpatialFact(subject_id="a", object_id="b", cover="none", provenance=decision),
                ObstacleSpatialFact(
                    subject_id="a", object_id="b", blocked=False, provenance=decision
                ),
            ),
        ),
        authenticated_actor_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="step",
            actor_id="a",
            expected_revision=4,
            encounter_id="fight",
            maneuver="ready",
            item_id="sword-a",
            basic_move=BasicMove(reference_actor_id="b", direction="approach"),
        ),
        authenticated_actor_id="a",
    )
    state = await load(play, cid)
    assert basic_distance(state.encounters[0], "a", "b") == 1
    spatial = state.encounters[0].spatial
    assert isinstance(spatial, BasicSpatialContext)
    reach = spatial.active("reach", "a", "b")
    assert isinstance(reach, ReachSpatialFact) and reach.relation == "reachable"
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_missing_visibility_and_blocked_retreat_require_gm_facts(tmp_path: Path) -> None:
    """B367/B377: the GM owns unknown spatial judgment and retreat feasibility."""
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    await service.execute(cid, start_basic(1, retreat=False), authenticated_actor_id="gm")
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
        ),
        authenticated_actor_id="a",
    )
    blocked = ChooseDefense(
        id="blocked-retreat",
        actor_id="b",
        expected_revision=2,
        encounter_id="fight",
        defense="dodge",
        basic_retreat=True,
    )
    with pytest.raises(ValidationError, match="Retreat is unavailable"):
        await service.execute(cid, blocked, authenticated_actor_id="b")

    declaration = DeclareBasicSpatialFacts(
        id="clear-retreat",
        actor_id="gm",
        expected_revision=2,
        encounter_id="fight",
        facts=(
            RetreatSpatialFact(
                subject_id="b",
                object_id="a",
                feasible=True,
                provenance=provenance(2, source="gm-adjudication", source_id="clear-retreat"),
            ),
        ),
    )
    await service.execute(cid, declaration, authenticated_actor_id="gm")
    await service.execute(
        cid,
        blocked.model_copy(update={"id": "legal-retreat", "expected_revision": 3}),
        authenticated_actor_id="b",
    )
    state = await load(play, cid)
    assert basic_distance(state.encounters[0], "a", "b") == 2
    spatial = state.encounters[0].spatial
    assert isinstance(spatial, BasicSpatialContext)
    assert spatial.active("visibility", "a", "b") is None
    assert await play.store.replay(cid) == await play.store.read(cid)

    with pytest.raises(ValidationError, match="GM authority"):
        await service.execute(
            cid,
            declaration.model_copy(
                update={"id": "player-fact", "actor_id": "a", "expected_revision": 4}
            ),
            authenticated_actor_id="a",
        )


async def test_conflicting_cover_and_obstacle_facts_fail_closed(tmp_path: Path) -> None:
    """B367: exact questions are GM facts; conflicts and unknowns do not invent geometry."""
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    conflicting = list(opening_facts(5))
    conflicting[1] = conflicting[1].model_copy(update={"relation": "reachable"})
    with pytest.raises(ValidationError, match="distance and reach"):
        await service.execute(
            cid,
            start_basic(5).model_copy(update={"facts": tuple(conflicting)}),
            authenticated_actor_id="gm",
        )

    blocked = list(opening_facts(5))
    blocked[5] = blocked[5].model_copy(update={"cover": "full"})
    blocked[6] = blocked[6].model_copy(update={"blocked": True})
    await service.execute(
        cid,
        start_basic(5).model_copy(update={"facts": tuple(blocked)}),
        authenticated_actor_id="gm",
    )
    with pytest.raises(ValidationError, match="Full cover"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="covered",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                target_id="b",
                item_id="sword-a",
            ),
            authenticated_actor_id="a",
        )
    with pytest.raises(ValidationError, match="obstacle"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="blocked",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="move",
                basic_move=BasicMove(reference_actor_id="b", direction="approach"),
            ),
            authenticated_actor_id="a",
        )

    decision = provenance(1, source="gm-adjudication", source_id="clear-path")
    await service.execute(
        cid,
        DeclareBasicSpatialFacts(
            id="clear-path",
            actor_id="gm",
            expected_revision=1,
            encounter_id="fight",
            facts=(
                CoverSpatialFact(subject_id="a", object_id="b", cover="none", provenance=decision),
                ObstacleSpatialFact(
                    subject_id="a", object_id="b", blocked=False, provenance=decision
                ),
            ),
        ),
        authenticated_actor_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="open-path",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="move",
            basic_move=BasicMove(reference_actor_id="b", direction="approach"),
        ),
        authenticated_actor_id="a",
    )
    assert basic_distance((await load(play, cid)).encounters[0], "a", "b") == 2


def test_director_intent_routes_basic_movement_without_flattening_it() -> None:
    command = Intent(
        kind="take_combat_turn",
        encounter_id="fight",
        maneuver="move",
        basic_reference_actor_id="b",
        basic_direction="approach",
    ).command("advance", "a", 7)
    assert command["basic_move"] == {
        "reference_actor_id": "b",
        "direction": "approach",
    }
    assert "basic_reference_actor_id" not in command

    with pytest.raises(ValidationError, match="both reference actor and direction"):
        Intent(
            kind="take_combat_turn",
            encounter_id="fight",
            maneuver="move",
            basic_reference_actor_id="b",
        ).command("invalid", "a", 7)
