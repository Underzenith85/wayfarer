"""B383 ordinary opening/closing as a real Ready, using authored object facts."""

import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.lock_channel_state import channels
from wayfarer.engine.simulation.magic.lock_state import (
    destroyed,
    latest,
    opening_allowed,
    save,
    validate_fixture,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.combat.encounter import Encounter
    from wayfarer.engine.simulation.combat.maneuver_rules import Declaration, Outcome


def validate_known_lock(
    state: PlayState,
    actor_id: str,
    target_id: str | None,
    maneuver: str,
    *,
    encounter: Encounter | None = None,
    hand: Hand | Literal["both"] | None = None,
) -> None:
    if maneuver != "ready" or target_id is None:
        return
    value = latest(state.resources).get(target_id)
    if value is None:
        raise ValidationError("Ready target is not an authored lock object")
    validate_fixture(state.world, state.resources, value.fixture)
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    known = {e.id for e in state.world.perspective(actor_id).entities} | set(actor.aware_of)
    if target_id not in known:
        raise ValidationError("Lock is not perceived")
    if encounter is not None:
        # deferred: the maneuver dispatcher and unarmed fighters share CombatEngine.
        from wayfarer.engine.simulation.combat.unarmed.fighters import free_hands

        available = free_hands(state, encounter, actor_id)
        requested = ("left-hand", "right-hand") if hand == "both" else (hand,) if hand else ()
        if not available or any(h not in available for h in requested):
            raise ValidationError("Operating a lock needs the selected usable free hand")


def ready_lock(declared: Declaration) -> Outcome:
    if declared.engine.rules.gurps_equipment is None:
        raise ValidationError("Lock Ready requires exact GURPS combat dispatch")
    if any(
        v is not None
        for v in (declared.item_id, declared.destination, declared.facing, declared.posture)
    ):
        raise ValidationError("Operating a lock requires a single uncombined Ready")
    value = latest(declared.resources).get(declared.target_id or "")
    if value is None:
        raise ValidationError("Ready target is not an authored lock object")
    channel = next(
        (
            c
            for c in channels(declared.resources)
            if c.target_id == declared.target_id and c.encounter_id == declared.encounter.id
        ),
        None,
    )
    if channel is None or channel.position is None or declared.battlefield is None:
        raise ValidationError("Combat lock operation requires authoritative object placement")
    if declared.battlefield.location_id not in (
        value.fixture.passage or (value.fixture.location_id,)
    ):
        raise ValidationError("Lock is outside this combat location")
    point = (
        Hex(q=channel.position[0], r=channel.position[1])
        if channel.geometry == "hex"
        else GridPoint(x=channel.position[0], y=channel.position[1])
    )
    if declared.engine.distance(declared.participant.position, point) > 1:
        raise ValidationError("Lock is out of reach")
    require_free_hand(declared.resources, declared.actor_id, declared.participant.hand_bindings)
    if (
        not destroyed(declared.resources, value)
        and value.closed
        and not opening_allowed(declared.resources, value)
    ):
        raise ConflictError("The lock prevents opening")
    # The shared grapple/close-combat DX check runs after this maneuver. Its
    # actual object change is committed only after that check has succeeded.
    return declared.participant.model_copy(update={"last_maneuver": "ready"}), declared.resources


def finish_lock_ready(state: PlayState, encounter: Encounter, command: TakeCombatTurn) -> PlayState:
    if command.maneuver != "ready" or command.target_id is None:
        return state
    validate_known_lock(
        state,
        command.actor_id,
        command.target_id,
        command.maneuver,
        encounter=encounter,
        hand=command.ready_hand,
    )
    key = hashlib.sha256(command.id.encode()).hexdigest()
    checked = next(
        (
            e
            for e in state.resources.events
            if e.id in ("grapple-ready:" + key, "close-ready:" + key)
        ),
        None,
    )
    if (
        checked is not None
        and not TypeAdapter(CheckTrace).validate_json(checked.kind).outcome.succeeded
    ):
        return state
    value = latest(state.resources)[command.target_id]
    if destroyed(state.resources, value):
        return state
    value = value.model_copy(
        update={
            "closed": not value.closed,
            "locked": not value.closed if value.fixture.kind == "lock" else value.locked,
        }
    )
    return state.model_copy(
        update={"resources": save(state.resources, value, command.id + ":lock")}
    )


def require_free_hand(
    resources: ResourceState, actor_id: str, bindings: tuple[tuple[str, Hand], ...]
) -> None:
    held = {h for _, h in bindings}
    lost = disabled(resources, actor_id)
    left = "left-hand" not in held and not (lost & {"left-arm", "left-hand"})
    right = "right-hand" not in held and not (lost & {"right-arm", "right-hand"})
    if not (left or right):
        raise ValidationError("Operating a lock needs a usable free hand")
