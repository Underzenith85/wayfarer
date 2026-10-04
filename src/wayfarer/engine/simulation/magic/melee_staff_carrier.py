"""Real manufactured Staff and uninterrupted equipped hand admission (B240/B481)."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.item_state import usable_item_enchantment
from wayfarer.engine.simulation.magic.melee_spell_state import Carrier, StaffCarrier
from wayfarer.engine.simulation.magic.staff_state import require_staff_construction
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.errors import ConflictError


def carrier_digest(
    state: PlayState, actor_id: str, carrier: Carrier, *, require_action: bool = False
) -> str:
    # deferred: canonical combat location queries cross the noun-to-verb architecture gate.
    from wayfarer.engine.simulation.combat.objects.locations import item_hands, unavailable_hand

    location = next(e.location_id for e in state.world.entities if e.id == actor_id)
    if require_action and not any(
        e.id.startswith("melee-spell:mana:") and e.target_id == location
        for e in state.resources.events
    ):
        raise ConflictError("Melee spell carrier requires current authenticated normal mana")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    if require_action and unavailable_hand(disabled(state.resources, actor_id), carrier.hand):
        raise ConflictError("Deathtouch carrier hand is unavailable")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if any(
        p.definition_id == "trait:disadvantage:no-manipulators"
        for p in actor.proposal.draft.purchases
    ):
        raise ConflictError("Deathtouch requires a usable hand")
    for encounter in state.encounters:
        if not require_action or encounter.status != "active":
            continue
        participant = next((p for p in encounter.participants if p.actor_id == actor_id), None)
        if participant and participant.pinned:
            raise ConflictError("Pinned caster cannot wield a Melee spell")
        if any(g.holder_id == actor_id and carrier.hand in g.hands for g in encounter.grips):
            raise ConflictError("Joint-grip Melee spell delivery is unsupported")
        if any(
            g.target_id == actor_id
            and g.location == ("left-arm" if carrier.hand == "left-hand" else "right-arm")
            for g in encounter.grips
        ):
            raise ConflictError("Grappled carrier arm cannot wield a Melee spell")
    if not isinstance(carrier, StaffCarrier):
        if any(
            carrier.hand in item_hands(state, actor_id, i.id)
            for i in state.resources.items
            if i.owner_id == actor_id and i.equipped
        ):
            raise ConflictError("Deathtouch requires a free charging hand")
        return hashlib.sha256(carrier.model_dump_json().encode()).hexdigest()
    item = next((i for i in state.resources.items if i.id == carrier.item_id), None)
    if (
        item is None
        or item.owner_id != actor_id
        or item.quantity != 1
        or (require_action and not item.ready)
        or not item.equipped
        or item.container_id is not None
        or not is_carried(state.resources, item)
    ):
        raise ConflictError("Deathtouch requires the currently wielded Staff")
    hands = item_hands(state, actor_id, item.id)
    if not hands:
        raise ConflictError("Deathtouch Staff is no longer held")
    if require_action and carrier.hand not in hands:
        raise ConflictError("Selected-hand Staff delivery after hand switching is unsupported")
    construction = require_staff_construction(state.resources, item.id)
    if item.definition_id != construction.definition_id:
        raise ConflictError("Deathtouch Staff construction changed")
    binding = next(
        (
            b
            for b in item.enchantments
            if b.spell_id == "staff"
            and b.runtime_family == "staff"
            and b.activation == "always-on"
            and b.always_on
            and usable_item_enchantment(state.resources, b, "normal")
        ),
        None,
    )
    if binding is None:
        raise ConflictError("Deathtouch requires an actual usable Staff enchantment")
    if hp.injury is None:
        raise ConflictError("Deathtouch requires current hand anatomy")
    payload = carrier.model_dump_json() + construction.model_dump_json() + binding.model_dump_json()
    return hashlib.sha256(payload.encode()).hexdigest()
