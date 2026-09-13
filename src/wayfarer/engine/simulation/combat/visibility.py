"""Authoritative encounter visibility shared by mechanics and projections."""

from typing import Literal

from wayfarer.engine.rules.types.tactical import CombatVisibility
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_visible
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, VisibilitySpatialFact
from wayfarer.engine.simulation.combat.tactical import sight
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError


def combat_visibility(encounter: Encounter, attacker_id: str, defender_id: str) -> CombatVisibility:
    """Derive B394 attack and defense limits from directed authoritative facts.

    Unknown-location attacks are not entity-targeted: callers must first persist a
    successful sensory location fact. This prevents guessed hidden identifiers
    from becoming an information oracle.
    """
    context = encounter.spatial
    if not isinstance(context, BasicSpatialContext):
        return CombatVisibility()
    attack = context.active("visibility", attacker_id, defender_id)
    defense = context.active("visibility", defender_id, attacker_id)
    if not isinstance(attack, VisibilitySpatialFact):
        raise ValidationError("Combat requires an authoritative visibility fact")
    attack_penalty: Literal[-10, -6, -4, 0]
    if attack.visible:
        attack_penalty = 0
    elif attack.obscuration == "blocked":
        raise ValidationError("Target is unavailable")
    elif not attack.location_known:
        raise ValidationError("Hidden target requires a successful sensory location fact")
    elif attack.obscuration == "invisible":
        attack_penalty = -6
    elif attack.obscuration in ("smoke", "darkness"):
        attack_penalty = -4
    else:  # pragma: no cover - exhaustive Literal guard
        raise ValidationError("Unsupported visibility condition")

    if not isinstance(defense, VisibilitySpatialFact) or defense.visible:
        return CombatVisibility(attack_penalty=attack_penalty)
    if defense.obscuration == "blocked" or not defense.aware_of_attack:
        return CombatVisibility(attack_penalty=attack_penalty, defenses=())
    if defense.location_known:
        return CombatVisibility(
            attack_penalty=attack_penalty,
            defense_penalty=-4,
        )
    return CombatVisibility(
        attack_penalty=attack_penalty,
        defense_penalty=-4,
        defenses=("dodge",),
    )


def targetable(encounter: Encounter, attacker_id: str, defender_id: str) -> bool:
    """Collapse all hidden-target failures to one non-disclosing predicate."""
    try:
        combat_visibility(encounter, attacker_id, defender_id)
    except ValidationError:
        return False
    return True


def visible_actors(
    state: PlayState, encounter: Encounter, actor_id: str, *, board: HexBattlefield | None = None
) -> frozenset[str]:
    own = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if own is None:
        return frozenset()
    entities = {e.id: e for e in state.world.perspective(actor_id).entities}
    own_entity = entities.get(actor_id)
    visible = {actor_id}
    for participant in encounter.participants:
        if (
            participant.actor_id == actor_id
            or participant.actor_id not in entities
            or own_entity is None
            or entities[participant.actor_id].location_id != own_entity.location_id
        ):
            continue
        try:
            observable = (
                basic_visible(encounter, actor_id, participant.actor_id)
                if isinstance(encounter.spatial, BasicSpatialContext)
                else sight(encounter, own, participant, board=board)
            )
        except ValidationError:
            observable = False
        if observable:
            visible.add(participant.actor_id)
    return frozenset(visible)
