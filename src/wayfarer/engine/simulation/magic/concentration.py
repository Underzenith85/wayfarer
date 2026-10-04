"""One concentration commitment per actor across supernatural services."""

from wayfarer.engine.simulation.ability_state import effects
from wayfarer.engine.simulation.health.condition_checks import retching_penalty
from wayfarer.engine.simulation.magic.analyze_magic_state import pending_actor_ids
from wayfarer.engine.simulation.magic.detect_magic_state import pending_actor_ids as detect_pending
from wayfarer.engine.simulation.magic.hand_melee_spell_state import held_actor_ids as hand_held
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    pending_actor_ids as hand_pending,
)
from wayfarer.engine.simulation.magic.limb_spell_state import held_actor_ids as limb_held
from wayfarer.engine.simulation.magic.limb_spell_state import pending_actor_ids as limb_pending
from wayfarer.engine.simulation.magic.melee_spell_state import held_actor_ids
from wayfarer.engine.simulation.magic.melee_spell_state import pending_actor_ids as melee_pending
from wayfarer.engine.simulation.magic.rooted_feet_state import active_caster_ids
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.wither_spell_state import held_actor_ids as wither_held
from wayfarer.engine.simulation.magic.wither_spell_state import pending_actor_ids as wither_pending
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


def require_idle_concentration(resources: ResourceState, actor_id: str) -> None:
    """Reject overlapping actions before injury rolls, fatigue or new receipts.

    Active maintained effects are not pending concentration. A pending cast
    stays a commitment until explicitly resolved, interrupted or cancelled,
    including after restart or a missed deadline.
    """

    if retching_penalty(resources, actor_id):
        raise ConflictError("Retching prevents concentration")
    if actor_id in detect_pending(resources):
        raise ConflictError("Actor is already concentrating on Detect Magic")
    if actor_id in hand_pending(resources):
        raise ConflictError("Actor is already concentrating on a hand Melee spell")
    if actor_id in melee_pending(resources):
        raise ConflictError("Actor is already concentrating on a Melee spell")
    if actor_id in wither_pending(resources):
        raise ConflictError("Actor is already concentrating on a Wither spell")
    if actor_id in limb_pending(resources):
        raise ConflictError("Actor is already concentrating on a limb spell")
    if actor_id in pending_actor_ids(resources):
        raise ConflictError("Actor is already concentrating on Analyze Magic")
    if any(e.actor_id == actor_id and e.concentrating for e in effects(resources)) or any(
        e.actor_id == actor_id and e.phase == "casting" for e in latest(resources).values()
    ):
        raise ConflictError("Actor is already concentrating")


def require_no_held_melee(resources: ResourceState, actor_id: str) -> None:
    if actor_id in active_caster_ids(resources):
        raise ConflictError("Active Rooted Feet spell-on composition is unsupported")
    if actor_id in (
        *held_actor_ids(resources),
        *hand_held(resources),
        *limb_held(resources),
        *wither_held(resources),
    ):
        raise ConflictError("Release or cancel the held Melee spell before casting another spell")
