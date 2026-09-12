"""Representation-specific reinforcement and Basic-to-hex escalation contracts."""

from pathlib import Path

import pytest
from test_basic_combat import opening_facts, start_basic
from test_encounter_context import load, setup
from test_gurps_melee import setup as melee_setup

from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.simulation.campaign.party import migrate as migrate_party
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    BasicSpatialFact,
    CoverSpatialFact,
    DistanceSpatialFact,
    ObstacleSpatialFact,
    ReachSpatialFact,
    RetreatSpatialFact,
    SpatialProvenance,
    VisibilitySpatialFact,
)
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield, Pose
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.combat import (
    BasicJoinPlacement,
    CombatService,
    HexJoinPlacement,
    HexPlacement,
    JoinEncounter,
    MigrateEncounterHex,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService

B_POSITION = Hex(q=2, r=0)


async def setup_profiled_basic(tmp_path: Path, distance: int) -> tuple[str, PlayService]:
    """Seed a Basic encounter under the exact profile required for hex combat."""
    cid, play = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        scene_bound=True,
    )

    def configure(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        original = state.encounters[0]
        participants = tuple(
            actor.model_copy(update={"position": None, "hex_facing": None})
            for actor in original.participants
            if actor.actor_id in ("a", "b")
        )
        order = tuple(actor.actor_id for actor in participants)
        encounter = original.model_copy(
            update={
                "spatial_context": BasicSpatialContext(facts=opening_facts(distance)),
                "participants": participants,
                "turn_order": order,
                "turn_index": order.index(original.current_actor_id),
            }
        )
        revision = state.revision + 1
        state = migrate_party(
            state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                    "encounters": (encounter,),
                }
            )
        )
        play.commit(campaign, state)
        return CommandReceipt(action="setup", outcome="profiled-basic")

    await play.store.commit_turn(cid, "profiled-basic", 1, "profiled-basic", configure)
    return cid, play


def reinforcement_facts(
    actor_id: str,
    others: tuple[str, ...],
    *,
    revision: int,
    command_id: str,
) -> tuple[
    DistanceSpatialFact
    | ReachSpatialFact
    | VisibilitySpatialFact
    | CoverSpatialFact
    | ObstacleSpatialFact
    | RetreatSpatialFact,
    ...,
]:
    origin = SpatialProvenance(
        source="gm-adjudication",
        source_id=command_id,
        declared_by="gm",
        declared_revision=revision,
    )
    facts: list[BasicSpatialFact] = []
    for index, other in enumerate(others, start=2):
        facts.extend(
            (
                DistanceSpatialFact(
                    subject_id=actor_id, object_id=other, yards=index, provenance=origin
                ),
                ReachSpatialFact(
                    subject_id=actor_id,
                    object_id=other,
                    relation="separated",
                    provenance=origin,
                ),
                ReachSpatialFact(
                    subject_id=other,
                    object_id=actor_id,
                    relation="separated",
                    provenance=origin,
                ),
                VisibilitySpatialFact(
                    subject_id=actor_id, object_id=other, visible=True, provenance=origin
                ),
                VisibilitySpatialFact(
                    subject_id=other, object_id=actor_id, visible=True, provenance=origin
                ),
                CoverSpatialFact(
                    subject_id=actor_id, object_id=other, cover="none", provenance=origin
                ),
                CoverSpatialFact(
                    subject_id=other, object_id=actor_id, cover="none", provenance=origin
                ),
                ObstacleSpatialFact(
                    subject_id=actor_id, object_id=other, blocked=False, provenance=origin
                ),
                ObstacleSpatialFact(
                    subject_id=other, object_id=actor_id, blocked=False, provenance=origin
                ),
                RetreatSpatialFact(
                    subject_id=actor_id, object_id=other, feasible=True, provenance=origin
                ),
                RetreatSpatialFact(
                    subject_id=other, object_id=actor_id, feasible=True, provenance=origin
                ),
            )
        )
    return tuple(facts)


def board(*, opaque: frozenset[Hex] = frozenset()) -> HexBattlefield:
    return HexBattlefield(
        id="basic-escalation",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id=BASELINE_ID,
        cells=tuple(
            Cell(
                position=Hex(q=q, r=r),
                opaque_height=2 if Hex(q=q, r=r) in opaque else 0,
            )
            for q in range(-3, 6)
            for r in range(-3, 4)
        ),
    )


def escalation(
    *,
    revision: int,
    b_position: Hex = B_POSITION,
    battlefield: HexBattlefield | None = None,
) -> MigrateEncounterHex:
    return MigrateEncounterHex(
        id="escalate",
        actor_id="gm",
        expected_revision=revision,
        encounter_id="fight",
        battlefield=battlefield or board(),
        placements=(
            HexPlacement(actor_id="a", pose=Pose(position=Hex(q=0, r=0), facing=0)),
            HexPlacement(actor_id="b", pose=Pose(position=b_position, facing=3)),
        ),
    )


async def test_same_group_basic_admission_is_complete_authoritative_and_replayable(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    await service.execute(cid, start_basic(2), authenticated_actor_id="gm")
    before = await load(play, cid)
    group = before.party.groups[0]
    current = before.encounters[0].current_actor_id
    with pytest.raises(ValidationError, match="GM authority"):
        await service.execute(
            cid,
            JoinEncounter(
                id="unauthorized-admission",
                actor_id="c",
                joining_actor_id="d",
                expected_revision=1,
                encounter_id="fight",
                placement=BasicJoinPlacement(
                    facts=reinforcement_facts(
                        "d", ("a", "b"), revision=1, command_id="unauthorized-admission"
                    )
                ),
            ),
            authenticated_actor_id="c",
        )
    with pytest.raises(ValidationError, match="complete fact set"):
        await service.execute(
            cid,
            JoinEncounter(
                id="incomplete-admission",
                actor_id="gm",
                joining_actor_id="c",
                expected_revision=1,
                encounter_id="fight",
                placement=BasicJoinPlacement(
                    facts=reinforcement_facts(
                        "c", ("a", "b"), revision=1, command_id="incomplete-admission"
                    )[:-1]
                ),
            ),
            authenticated_actor_id="gm",
        )
    command = JoinEncounter(
        id="admit-c",
        actor_id="gm",
        joining_actor_id="c",
        expected_revision=1,
        encounter_id="fight",
        placement=BasicJoinPlacement(
            facts=reinforcement_facts("c", ("a", "b"), revision=1, command_id="admit-c")
        ),
    )
    await service.execute(cid, command, authenticated_actor_id="gm")
    state = await load(play, cid)
    encounter = state.encounters[0]
    assert isinstance(encounter.spatial, BasicSpatialContext)
    assert set(encounter.turn_order) == {"a", "b", "c"}
    assert encounter.current_actor_id == current
    assert state.party.groups[0] == group
    assert all("position" not in p for p in encounter.model_dump(mode="json")["participants"])

    restarted = CombatService(PlayService(play.store, play.engine))
    assert await restarted.execute(
        cid, command, authenticated_actor_id="gm"
    ) == await service.execute(cid, command, authenticated_actor_id="gm")
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_hex_join_requires_visibility_and_profile_compatibility(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    await service.execute(cid, start_basic(2), authenticated_actor_id="gm")
    with pytest.raises(ValidationError, match="exact saved combat profile"):
        await service.execute(cid, escalation(revision=1), authenticated_actor_id="gm")

    cid, play = await setup_profiled_basic(tmp_path, 2)
    wall = frozenset(
        {
            Hex(q=5, r=2),
            Hex(q=5, r=1),
            Hex(q=4, r=1),
            Hex(q=3, r=2),
            Hex(q=3, r=3),
            Hex(q=4, r=3),
        }
    )
    await CombatService(play).execute(
        cid,
        escalation(revision=2, battlefield=board(opaque=wall)),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    with pytest.raises(ValidationError, match="visible"):
        await CombatService(play).execute(
            cid,
            JoinEncounter(
                id="hidden-c",
                actor_id="c",
                expected_revision=3,
                encounter_id="fight",
                placement=HexJoinPlacement(position=Hex(q=4, r=2), facing=3),
            ),
            authenticated_actor_id="c",
        )


async def test_basic_escalation_preserves_turn_and_hex_join_rejects_occupancy(
    tmp_path: Path,
) -> None:
    cid, play = await setup_profiled_basic(tmp_path, 2)
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="a-acted",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="a",
    )
    before = (await load(play, cid)).encounters[0]
    with pytest.raises(ValidationError, match="distance"):
        await service.execute(
            cid, escalation(revision=3, b_position=Hex(q=3, r=0)), authenticated_actor_id="gm"
        )
    await service.execute(cid, escalation(revision=3), authenticated_actor_id="gm")
    play = play.for_campaign(await play.store.read(cid))
    service = CombatService(play)
    state = await load(play, cid)
    encounter = state.encounters[0]
    assert encounter.spatial_kind == "hex"
    assert encounter.current_actor_id == before.current_actor_id
    assert encounter.round == before.round
    assert tuple(p.maneuver_state for p in encounter.participants) == tuple(
        p.maneuver_state for p in before.participants
    )

    with pytest.raises(ValidationError, match="blocked, occupied or out of bounds"):
        await service.execute(
            cid,
            JoinEncounter(
                id="occupied-c",
                actor_id="c",
                expected_revision=4,
                encounter_id="fight",
                placement=HexJoinPlacement(position=Hex(q=0, r=0), facing=0),
            ),
            authenticated_actor_id="c",
        )
    join = JoinEncounter(
        id="hex-c",
        actor_id="c",
        expected_revision=4,
        encounter_id="fight",
        placement=HexJoinPlacement(position=Hex(q=1, r=1), facing=3),
    )
    await service.execute(cid, join, authenticated_actor_id="c")
    joined = await load(play, cid)
    assert set(joined.encounters[0].turn_order) == {"a", "b", "c"}
    assert joined.encounters[0].current_actor_id == before.current_actor_id
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_basic_escalation_rejects_pending_interaction(tmp_path: Path) -> None:
    cid, play = await setup_profiled_basic(tmp_path, 1)
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
            mode_id="swing",
        ),
        authenticated_actor_id="a",
    )
    with pytest.raises(ConflictError, match="pending interaction"):
        await service.execute(cid, escalation(revision=3), authenticated_actor_id="gm")
