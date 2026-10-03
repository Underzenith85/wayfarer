"""B368/B377 legal mapped ground Steps; no inferred aerial or aquatic route."""

from collections import deque

from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.generations import ground_dive_step_enabled
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.engine.simulation.combat.tactical import move_hex
from wayfarer.engine.simulation.hex_geometry import Hex, neighbor
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def validate_ground_step(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    response: BlastResponse,
    environment: str,
) -> None:
    if not ground_dive_step_enabled() or response.dive_to is None:
        return
    actor = next(p for p in encounter.participants if p.actor_id == response.actor_id)
    if environment == "water" or (actor.personal_flight and actor.personal_flight.altitude > 0):
        return
    if (
        actor.grappled
        or actor.pinned
        or any(g.holder_id == actor.actor_id for g in encounter.grips)
    ):
        raise ValidationError("Release or escape the grapple before moving")
    allowance = movement(runtime, state, actor.actor_id)
    step = max(1, (allowance + 9) // 10)
    destination = response.dive_to
    if isinstance(actor.position, Hex):
        assert destination.x is not None and destination.y is not None
        target = Hex(q=destination.x, r=destination.y)
        board = runtime.require_hex(encounter)
        walker = actor.model_copy(update={"movement_allowance": allowance})
        queue: deque[tuple[Hex, tuple[Hex, ...]]] = deque([(actor.position, ())])
        seen = {actor.position}
        while queue:
            point, path = queue.popleft()
            if point == target:
                return
            if len(path) == step:
                continue
            for direction in range(6):
                try:
                    following = neighbor(point, direction)
                    if following in seen:
                        continue
                    route = (*path, following)
                    move_hex(
                        encounter,
                        walker,
                        "attack",
                        route,
                        None,
                        None,
                        response.sacrificial_contact and following == target,
                        board=board,
                    )
                except ValidationError, ValueError:
                    continue
                seen.add(following)
                queue.append((following, route))
    elif isinstance(actor.position, GridPoint):
        assert destination.x is not None and destination.y is not None
        square_target = GridPoint(x=destination.x, y=destination.y)
        assert runtime.rules.combat is not None
        assert isinstance(encounter.spatial, SquareSpatialContext)
        square_board = next(
            b for b in runtime.rules.combat.battlefields if b.id == encounter.spatial.battlefield_id
        )
        assert isinstance(square_board, Battlefield)
        blocked = set(square_board.blocked)
        occupants = {p.position for p in encounter.participants if p.actor_id != actor.actor_id}
        enemies = {
            p.position
            for p in encounter.participants
            if p.actor_id != actor.actor_id and encounter.blocks_passage(actor.actor_id, p.actor_id)
        }
        square_queue = deque([(actor.position, 0)])
        visited = {actor.position}
        while square_queue:
            square_point, spent = square_queue.popleft()
            if square_point == square_target:
                return
            if spent == step:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                x, y = square_point.x + dx, square_point.y + dy
                if not 0 <= x < square_board.width or not 0 <= y < square_board.height:
                    continue
                square_following = GridPoint(x=x, y=y)
                contact = response.sacrificial_contact and square_following == square_target
                if (
                    square_following in visited
                    or square_following in blocked
                    or square_following in enemies
                    and not contact
                    or square_following == square_target
                    and square_following in occupants
                    and not contact
                ):
                    continue
                visited.add(square_following)
                square_queue.append((square_following, spent + 1))
    raise ValidationError("No legal ground diving step reaches the declared destination")
