"""Authoritative encounter visibility shared by mechanics and projections."""

from typing import Literal

from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.tactical import CombatVisibility
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_visible
from wayfarer.engine.simulation.combat.sensory_state import evidence
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, VisibilitySpatialFact
from wayfarer.engine.simulation.combat.tactical import sight
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError


def _geometric_visibility(
    encounter: Encounter,
    attacker_id: str,
    defender_id: str,
    *,
    ignore_visual_location: bool = False,
) -> CombatVisibility:
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
    attack_penalty: Literal[-10, -6, -4, 0]
    if not isinstance(attack, VisibilitySpatialFact):
        if not ignore_visual_location:
            raise ValidationError("Combat requires an authoritative visibility fact")
        attack_penalty = 0
    elif not attack.visible and attack.obscuration == "blocked":
        raise ValidationError("Target is unavailable")
    elif attack.visible or ignore_visual_location:
        attack_penalty = 0
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


def _nonvisual(
    state: PlayState, encounter: Encounter, observer_id: str, target_id: str
) -> tuple[bool, bool, bool]:
    """Return located, aware-of-attack and exact-location facts, never coordinates."""
    proof = evidence(state, encounter, observer_id, target_id)
    if proof is not None:
        return proof.located, proof.aware_of_attack, proof.exact_location is not None
    if isinstance(encounter.spatial, BasicSpatialContext):
        fact = encounter.spatial.active("visibility", observer_id, target_id)
        if (
            isinstance(fact, VisibilitySpatialFact)
            and not fact.visible
            and fact.obscuration != "blocked"
        ):
            # Legacy Basic facts can establish a sensory location/awareness,
            # but their boolean cannot prove the narrower B394 certainty case.
            return fact.location_known, fact.aware_of_attack, False
    return False, False, False


def combat_visibility(
    encounter: Encounter,
    attacker_id: str,
    defender_id: str,
    *,
    state: PlayState | None = None,
    validate_attack: bool = True,
) -> CombatVisibility:
    """B394 geometry and current visual capacity have separate authorities."""
    attack_blind = state is not None and acute_blindness(state.resources, attacker_id)
    ordinary = _geometric_visibility(
        encounter,
        attacker_id,
        defender_id,
        ignore_visual_location=attack_blind or not validate_attack,
    )
    if state is None:
        return ordinary
    attack_penalty = ordinary.attack_penalty
    defense_penalty = ordinary.defense_penalty
    defenses = ordinary.defenses
    if attack_blind and validate_attack:
        located, _, exact = _nonvisual(state, encounter, attacker_id, defender_id)
        if not located:
            raise ValidationError("Blind attack requires current nonvisual target location")
        attack_penalty = -4 if exact else -10
    if acute_blindness(state.resources, defender_id):
        located, aware, _ = _nonvisual(state, encounter, defender_id, attacker_id)
        defenses = () if not aware else ("dodge", "parry", "block") if located else ("dodge",)
        defense_penalty = -4 if defenses else 0
    return CombatVisibility(
        attack_penalty=attack_penalty,
        defense_penalty=defense_penalty,
        defenses=defenses,
    )


def external_defense_penalty(state: PlayState, defender_id: str, penalty: int) -> int:
    """B394's -4 is already in canonical blind defense scores; apply it once."""
    return 0 if acute_blindness(state.resources, defender_id) else penalty


def targetable(
    encounter: Encounter,
    attacker_id: str,
    defender_id: str,
    *,
    state: PlayState | None = None,
) -> bool:
    """Collapse all hidden-target failures to one non-disclosing predicate."""
    try:
        combat_visibility(encounter, attacker_id, defender_id, state=state)
    except ValidationError:
        return False
    return True


def visible_actors(
    state: PlayState, encounter: Encounter, actor_id: str, *, board: HexBattlefield | None = None
) -> frozenset[str]:
    own = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if own is None:
        return frozenset()
    if acute_blindness(state.resources, actor_id):
        return frozenset({actor_id})
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


def melee_eye_penalty(state: PlayState, actor_id: str) -> int:
    """Preserve eye-injury scores unless acute blindness already replaces them."""
    if acute_blindness(state.resources, actor_id):
        return 0
    eyes = disabled(state.resources, actor_id) & {"left-eye", "right-eye"}
    return 6 if len(eyes) == 2 else 1 if eyes else 0


def optical_bonus(state: PlayState, actor_id: str, bonus: int) -> int:
    """An otherwise detected target does not make optical sights usable."""
    return 0 if acute_blindness(state.resources, actor_id) else bonus


def adjusted_defense(value: DerivedValue | None, penalty: int) -> DerivedValue | None:
    """Apply an admitted external visibility contribution to a real defense."""
    return (
        None
        if value is None
        else DerivedValue(value.target, value.value + penalty, value.explanations)
    )
