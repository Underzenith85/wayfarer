"""Pure B240 named-hand productive admission; no charge lifetime transitions."""

from dataclasses import replace

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.melee_spell_state import ObserveMeleeMana, identifier
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import require_native_size
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError


def require_productive_hand(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    hand: Hand,
    build_revision: str | None = None,
) -> ValidatedBuild:
    """Return the current approved build for an explicitly empty usable hand.

    Optional build_revision is an immutable approved-source revision comparison,
    not caller authority or a substitute for current approval. This query never
    spends, dissipates or creates a charge. Casting learning, ritual, elapsed time
    and attack legality are responsibilities of the eventual actual producer.
    """
    if hand not in ("left-hand", "right-hand"):
        raise ConflictError("Melee hand requires one exact named human hand")
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    if (
        actor is None
        or actor.body is None
        or actor.body.anatomy != "human"
        or actor.body.tolerance is not None
        or hp is None
        or hp.injury is None
        or hp.injury.anatomy != "human"
        or hp.injury.tolerance is not None
        or hp.injury.machine
    ):
        raise ConflictError("Melee hand requires current ordinary human anatomy")
    require_native_size(state.resources, actor_id)
    if hp.injury.incapacitated or hp.injury.stunned or not fatigue_ready(state, actor_id):
        raise ConflictError("Melee hand requires a conscious capable actor")
    # A captured attack build cannot authorize this new productive-use query.
    compiled = build(replace(runtime, attack_source=None), state, actor_id)
    if build_revision is not None and compiled.revision != build_revision:
        raise ConflictError("Melee hand approved source revision changed")
    if any(
        any(
            token in p.definition_id
            for token in (
                "no-manipulators",
                "no-fine-manipulators",
                "extra-arms",
                "alternate-form",
                "morph",
                "insubstantial",
            )
        )
        for p in compiled.purchases
    ):
        raise ConflictError("Melee hand body adaptation is unsupported")
    entity = next((e for e in state.world.entities if e.id == actor_id), None)
    location = (
        entity.location_id if entity is not None and entity.kind is EntityKind.ACTOR else None
    )
    if location is None or not any(
        e.id == location and e.kind is EntityKind.LOCATION for e in state.world.entities
    ):
        raise ConflictError("Melee hand requires an actual current world location")
    witnesses = tuple(
        e
        for e in state.resources.events
        if e.id.startswith("melee-spell:mana:") and e.target_id == location
    )
    if len(witnesses) != 1:
        raise ConflictError("Melee hand requires current authenticated normal mana")
    observation = ObserveMeleeMana.model_validate_json(witnesses[0].kind)
    if observation.location_id != location or witnesses[0].id != identifier("mana", observation.id):
        raise ConflictError("Melee hand mana witness identity changed")
    # deferred: canonical combat hand queries cross the noun-to-verb architecture gate.
    from wayfarer.engine.simulation.combat.objects.locations import item_hands, unavailable_hand

    if unavailable_hand(disabled(state.resources, actor_id), hand):
        raise ConflictError("Melee hand is currently unavailable")
    if any(
        hand in item_hands(state, actor_id, item.id)
        for item in state.resources.items
        if item.owner_id == actor_id and item.equipped
    ):
        raise ConflictError("Melee hand must currently be empty")
    for encounter in state.encounters:
        if encounter.status != "active":
            continue
        participant = next((p for p in encounter.participants if p.actor_id == actor_id), None)
        if participant is None:
            continue
        if participant.pinned:
            raise ConflictError("Melee hand control adaptation is unsupported")
        if any(g.holder_id == actor_id and hand in g.hands for g in encounter.grips):
            raise ConflictError("Melee hand is occupied by a control grip")
        arm = hand.replace("hand", "arm")
        if any(g.target_id == actor_id and g.location == arm for g in encounter.grips):
            raise ConflictError("Melee hand arm is currently grappled")
    return compiled
