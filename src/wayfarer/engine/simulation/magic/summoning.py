"""Authored placements for the B236 critical-failure malign appearance."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import CombatAllegiance, Encounter, SideOpposition
from wayfarer.engine.simulation.combat.settlement import combat_ready
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.vocabulary import Facing
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, HexFacing
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record


class SummonEncounter(Record):
    """Private GM declaration; never inferred from narrative or a caster roll."""

    encounter_id: Id
    battlefield_id: Id
    caster: Placement
    scene_id: Id | None = None
    summoned_facing: Facing = "north"
    summoned_hex_facing: HexFacing | None = None


def validate_summon(
    runtime: RulesContext,
    state: PlayState,
    *,
    caster_id: str,
    location_id: str | None,
    target_id: str,
    position: tuple[int, int] | None,
    encounter: Encounter | None,
    declaration: SummonEncounter | None,
) -> None:
    rules = runtime.rules.combat
    if rules is None or position is None or target_id == caster_id:
        raise ValidationError("Summoning requires an approved reserve combatant and placement")
    if any(e.status == "active" and target_id in e.turn_order for e in state.encounters):
        raise ValidationError("Summoned actor is already in an active encounter")
    actor = next(a for a in state.actors if a.actor_id == target_id)
    if (
        actor.conditions
        or actor.available_at > state.resources.game_time
        or not combat_ready(state, target_id, gurps=rules.gurps_equipment is not None)
    ):
        raise ValidationError("Summoned malign actor cannot attack")
    if encounter is not None:
        if (
            declaration is not None
            or encounter.status != "active"
            or encounter.spatial_kind == "basic"
        ):
            raise ValidationError("Summoning requires the existing active mapped encounter")
        board_id = encounter.battlefield_id
        occupied = tuple(p.position for p in encounter.participants)
    else:
        if declaration is None or declaration.caster.actor_id != caster_id:
            raise ValidationError(
                "Noncombat summoning requires GM-authored caster and battlefield placement"
            )
        if any(e.id == declaration.encounter_id for e in state.encounters):
            raise ValidationError("Summoning encounter ID already exists")
        if any(e.status == "active" and caster_id in e.turn_order for e in state.encounters):
            raise ValidationError("Caster already belongs to another active encounter")
        entity = next(e for e in state.world.entities if e.id == caster_id)
        if entity.location_id != location_id:
            raise ValidationError("Caster left the failed spell location")
        board_id = declaration.battlefield_id
        occupied = (declaration.caster.position,)
    board = next((b for b in rules.battlefields if b.id == board_id), None)
    if board is None or board.location_id != location_id:
        raise ValidationError("Summoning battlefield does not match the failed spell location")
    point = (
        Hex(q=position[0], r=position[1])
        if isinstance(board, HexBattlefield)
        else GridPoint(x=position[0], y=position[1])
    )
    if point in occupied:
        raise ValidationError("Summoned combatant placement is occupied")
    if isinstance(board, HexBattlefield):
        assert isinstance(point, Hex)
        if board.cell(point).blocked:
            raise ValidationError("Summoned combatant placement is blocked")
    elif (
        isinstance(point, GridPoint)
        and isinstance(board, Battlefield)
        and (point.x >= board.width or point.y >= board.height or point in board.blocked)
    ):
        raise ValidationError("Summoned combatant placement is outside the battlefield")


def hostile_allegiance(
    encounter: Encounter, caster_id: str, target_id: str, identifier: str
) -> Encounter:
    """Keep authored sides, classify the newcomer, and record B236 hostility."""
    sides = {a.actor_id: a.side_id for a in encounter.allegiances}
    used = {a.side_id for a in encounter.allegiances}
    # Stable source-owned IDs are scoped by the unique backfire record.
    caster_side = sides.get(caster_id) or "summon-caster:" + identifier
    target_side = "summon-foe:" + identifier
    while target_side in used or target_side == caster_side:
        target_side += ":new"
    sides[caster_id], sides[target_id] = caster_side, target_side
    pair = tuple(sorted((caster_side, target_side)))
    opposition = SideOpposition(side_ids=(pair[0], pair[1]))
    return encounter.model_copy(
        update={
            "allegiances": tuple(
                CombatAllegiance(actor_id=p.actor_id, side_id=sides.get(p.actor_id))
                for p in encounter.participants
            ),
            "oppositions": encounter.oppositions + (opposition,),
            "completion_policy": "gm"
            if encounter.completion_policy == "legacy"
            else encounter.completion_policy,
        }
    )
