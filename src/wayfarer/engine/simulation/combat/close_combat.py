"""Close-combat state and deterministic procedures from Campaigns B391-B392."""

from __future__ import annotations

from collections.abc import Iterable

from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.hex_geometry import Hex, arc
from wayfarer.errors import ValidationError


def pair(left: str, right: str) -> tuple[str, str]:
    if left == right:
        raise ValidationError("Close combat requires two combatants")
    return min(left, right), max(left, right)


def engaged(encounter: Encounter, left: str, right: str) -> bool:
    return pair(left, right) in encounter.close_pairs


def opponents_in_close_combat(encounter: Encounter, actor_id: str) -> tuple[str, ...]:
    return tuple(
        candidate
        for relation in encounter.close_pairs
        if actor_id in relation
        for candidate in relation
        if candidate != actor_id
    )


def enter(encounter: Encounter, actor: Combatant, target_id: str) -> Encounter:
    """Commit an already-validated same-hex entry and every resulting relationship."""
    target = next((p for p in encounter.participants if p.actor_id == target_id), None)
    if target is None or target.actor_id == actor.actor_id:
        raise ValidationError("Close-combat entry requires another combatant")
    if isinstance(encounter.spatial, BasicSpatialContext):
        relationships = {pair(actor.actor_id, target.actor_id)}
    else:
        if actor.position != target.position:
            raise ValidationError("Close-combat entry must end in the target hex")
        relationships = {
            pair(actor.actor_id, other.actor_id)
            for other in encounter.participants
            if other.actor_id != actor.actor_id
            and actor.position in encounter.occupied_hexes(other.actor_id)
        }
    return encounter.model_copy(
        update={"close_pairs": tuple(sorted(set(encounter.close_pairs) | relationships))}
    )


def leave(encounter: Encounter, actor: Combatant, path: tuple[Hex, ...]) -> None:
    """Validate the supported non-evasion exit through the actor's own half of the hex."""
    foes = opponents_in_close_combat(encounter, actor.actor_id)
    if not foes or not path:
        return
    if actor.grappled:
        raise ValidationError("Break free before leaving close combat")
    if arc(pose(actor), path[0]) == "front":
        raise ValidationError("Leaving through a foe's side requires an explicit evasion")


def remove_departed_pairs(encounter: Encounter, actor_id: str) -> Encounter:
    if isinstance(encounter.spatial, BasicSpatialContext):
        return encounter
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    retained = tuple(
        relation
        for relation in encounter.close_pairs
        if actor_id not in relation
        or actor.position
        in encounter.occupied_hexes(next(value for value in relation if value != actor_id))
    )
    return encounter.model_copy(update={"close_pairs": retained})


def validate_defense(*, defense: str, parry_reaches: Iterable[int] = ()) -> None:
    if defense == "block":
        raise ValidationError("Blocking is unavailable in close combat")
    if defense == "parry" and 0 not in parry_reaches:
        raise ValidationError("An armed close-combat parry requires Reach C")


def stray_target_order(candidates: Iterable[str], selectors: Iterable[int]) -> tuple[str, ...]:
    """Choose friendly-risk order solely from recorded entropy, before resolution."""
    remaining = sorted(set(candidates))
    values = tuple(selectors)
    if len(values) < len(remaining):
        raise ValidationError("Close-combat stray targeting requires recorded entropy")
    ordered: list[str] = []
    for value in values[: len(remaining)]:
        if value < 1 or value > 6:
            raise ValidationError("Close-combat target selectors must be d6 results")
        ordered.append(remaining.pop((value - 1) % len(remaining)))
    return tuple(ordered)


def cooperative_control_score(best: int, helpers: Iterable[int], *, pin: bool) -> int:
    """Combine the best controller with the permitted helpers (B392)."""
    additions = tuple(helpers)
    maximum_helpers = 2 if pin else 1
    if len(additions) > maximum_helpers or min((best, *additions), default=0) < 0:
        raise ValidationError("Too many helpers or an invalid control score")
    return best + sum(score // 5 for score in additions)
