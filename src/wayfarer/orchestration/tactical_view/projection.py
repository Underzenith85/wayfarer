"""Allowlisted player projections of an encounter: what the transport is allowed to serve."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.combat.visibility import visible_actors
from wayfarer.engine.simulation.hex_geometry import HexBattlefield, SightPoint, line_of_sight
from wayfarer.orchestration.combat import ChooseDefense, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.hex_choices import choices
from wayfarer.orchestration.tactical_view.records import (
    TacticalActor,
    TacticalEncounter,
    TacticalGrip,
    TacticalSnapshot,
)

if TYPE_CHECKING:
    from wayfarer.orchestration.access import CampaignAccess


def legacy_encounter(
    state: PlayState,
    encounter: Encounter,
    member: CampaignMember,
    *,
    board: HexBattlefield | None = None,
) -> dict[str, object]:
    """Legacy consumers need turn/defense controls, never raw engine snapshots."""
    visible = frozenset(
        a
        for owner in member.actor_ids
        for a in visible_actors(state, encounter, owner, board=board)
    )
    order = tuple(a for a in encounter.turn_order if a in visible)
    pending = encounter.pending_defense
    return {
        "id": encounter.id,
        "status": encounter.status,
        "round": encounter.round,
        "turn_order": order,
        "turn_index": order.index(encounter.current_actor_id)
        if encounter.current_actor_id in order
        else -1,
        "pending_defense": {"defender_id": pending.defender_id, "allowed": pending.allowed}
        if pending and pending.defender_id in member.actor_ids
        else None,
    }


async def snapshot(
    access: CampaignAccess, cid: str, principal: str, actor_id: str
) -> TacticalSnapshot:
    access = await access.runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    member = access._member(state, principal)
    access._control(member, actor_id)
    return project(access.play, state, member, actor_id)


def project(
    play: PlayService,
    state: PlayState,
    member: CampaignMember,
    actor_id: str,
    *,
    include_object_choices: bool = False,
) -> TacticalSnapshot:
    views: list[TacticalEncounter] = []
    entities = {e.id: e for e in state.world.entities}
    for encounter in state.encounters:
        board = play.rules_context.hex_map(encounter)
        if board is None or actor_id not in encounter.turn_order:
            continue
        visible = visible_actors(
            state, encounter, actor_id, board=play.rules_context.hex_map(encounter)
        )
        own = next(p for p in encounter.participants if p.actor_id == actor_id)

        cells = tuple(
            cell
            for cell in board.cells
            if line_of_sight(
                board,
                SightPoint(position=pose(own).position, height=1 if own.posture != "prone" else 0),
                SightPoint(position=cell.position, height=1),
            )
        )
        views.append(
            TacticalEncounter(
                id=encounter.id,
                status=encounter.status,
                round=encounter.round,
                current_actor_id=encounter.current_actor_id
                if encounter.current_actor_id in visible
                else None,
                cells=cells,
                actors=tuple(
                    TacticalActor(
                        id=p.actor_id,
                        name=entities[p.actor_id].name,
                        position=pose(p).position,
                        facing=pose(p).facing,
                        posture=p.posture,
                        controlled=p.actor_id == actor_id,
                        grappled=p.grappled,
                        pinned=p.pinned,
                    )
                    for p in encounter.participants
                    if p.actor_id in visible
                ),
                grips=tuple(
                    TacticalGrip(
                        id=g.id,
                        holder_id=g.holder_id,
                        target_id=g.target_id,
                        location=g.location,
                        hands=g.hands,
                    )
                    for g in encounter.grips
                    if g.holder_id in visible and g.target_id in visible
                ),
                choices=tuple(
                    c
                    for c in choices(play, state, encounter, actor_id, visible)
                    if include_object_choices
                    or not (
                        isinstance(c.command, TakeCombatTurn)
                        and c.command.target_item_id
                        or isinstance(c.command, ChooseDefense)
                        and c.command.item_id in ("left-hand", "right-hand")
                        and encounter.pending_defense is not None
                    )
                )
                if state.lifecycle == "active"
                else (),
                notice="This encounter requires resolution of an unsupported rule."
                if encounter.blocked_reason
                else None,
                traces=tuple(t for t in encounter.tactical_traces if t.actor_id == actor_id),
            )
        )
    return TacticalSnapshot(
        campaign_id=state.campaign_id,
        actor_id=actor_id,
        revision=state.revision,
        encounters=tuple(views),
    )
