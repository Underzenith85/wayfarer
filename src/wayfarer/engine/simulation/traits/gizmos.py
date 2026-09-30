"""B57 ordinary Gizmos: GM-authored eligibility and session-bounded revelation.

Unrevealed equipment is deliberately absent from ResourceState.items. Once
revealed it is ordinary inventory, subject to weight, custody, damage and loss.
Gadgeteer inventions/build-on-the-spot (B58) are not implemented here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    Command,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "gizmo:"
DEFINITION_ID = "trait:advantage:gizmos"


class GizmoEligibility(Record):
    """Trusted GM decision, never accepted as part of a player's command.

    `owned` carries the actual undeclared owned instance, preserving its ID and
    condition. The other categories carry the GM-approved new item instance.
    There is no numerical pocket-size/price threshold in B57: these facts are
    explicitly adjudicated, rather than inferred from weight or market price.
    """

    id: Id
    actor_id: Id
    session_id: Id
    item: Item
    category: Literal["owned", "character-concept", "common-device"]
    pocket_sized: bool
    could_have_carried: bool
    owned_but_undeclared: bool = False
    probably_owned: bool = False
    matches_character_concept: bool = False
    minor_or_ignorable: bool = False
    inexpensive: bool = False
    widely_available_at_actor_tl: bool = False
    # No Gadgeteer extension is silently admitted as ordinary equipment.
    gadgeteer_invention: bool = False


class BeginGizmoSession(Command):
    kind: Literal["begin-gizmo-session"] = "begin-gizmo-session"
    session_id: Id


class RevealGizmo(Command):
    kind: Literal["reveal-gizmo"] = "reveal-gizmo"
    session_id: Id
    eligibility_id: Id


class GizmoOutcome(Record):
    command_id: Id
    actor_id: Id
    session_id: Id
    eligibility_id: Id | None = None
    item_id: Id | None = None
    uses_remaining: int | None = None


def history(state: ResourceState) -> tuple[GizmoOutcome, ...]:
    return tuple(
        GizmoOutcome.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def _prior(state: ResourceState, command: BeginGizmoSession | RevealGizmo) -> GizmoOutcome | None:
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt is None:
        if command.expected_revision != state.revision:
            raise ConflictError("Gizmo revision changed")
        return None
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    if receipt.digest != digest:
        raise ConflictError("Gizmo command ID was already used")
    event = next((e for e in state.events if e.id == PREFIX + command.id), None)
    if event is None:
        raise ConflictError("Command ID belongs to another procedure")
    return GizmoOutcome.model_validate_json(event.kind)


def _commit(
    state: ResourceState,
    command: BeginGizmoSession | RevealGizmo,
    outcome: GizmoOutcome,
    items: tuple[Item, ...],
) -> ResourceState:
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "items": items,
            "receipts": state.receipts
            + (
                Receipt(
                    command_id=command.id,
                    digest=hashlib.sha256(command.model_dump_json().encode()).hexdigest(),
                ),
            ),
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + command.id,
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=outcome.model_dump_json(),
                ),
            ),
        }
    )


def begin_session(
    state: ResourceState,
    command: BeginGizmoSession,
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, GizmoOutcome]:
    """Only the trusted GM establishes a new game session; time never resets uses.

    Session IDs cannot be reopened. Retrying the exact command is harmless.
    Items from earlier sessions remain in play and retain their current custody.
    """
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Gizmo session reset requires GM authority")
    prior = _prior(state, command)
    if prior is not None:
        return state, prior
    if any(e.session_id == command.session_id for e in history(state)):
        raise ConflictError("Gizmo game session has already been opened")
    outcome = GizmoOutcome(
        command_id=command.id, actor_id=command.actor_id, session_id=command.session_id
    )
    return _commit(state, command, outcome, state.items), outcome


def reveal_gizmo(
    engine: ResourceEngine,
    state: ResourceState,
    command: RevealGizmo,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    eligibility: tuple[GizmoEligibility, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, GizmoOutcome]:
    """Atomically create one eligible actual instance and consume one session use.

    Authority is checked before receipt replay. GM eligibility is trusted input;
    commands contain only its stable ID. Revelation is deterministic, with no
    dice roll or random ID. Unimplemented Gadgeteer crafting fails closed.
    """
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Gizmo revelation requires actor and GM authority")
    prior = _prior(state, command)
    if prior is not None:
        return state, prior
    events = history(state)
    sessions = tuple(e.session_id for e in events if e.item_id is None)
    if not sessions or sessions[-1] != command.session_id:
        raise ValidationError("Gizmo use requires the current GM-opened game session")
    effects = mundane_trait_effects(build, definitions)
    levels = sum(e.levels for e in effects if e.definition_id == DEFINITION_ID)
    if not 1 <= levels <= 3:
        raise ValidationError("Ordinary Gizmos require one to three purchased levels")
    spent = sum(
        e.item_id is not None
        and e.actor_id == command.actor_id
        and e.session_id == command.session_id
        for e in events
    )
    if spent >= levels:
        raise ValidationError("Gizmo uses exhausted for this game session")
    matches = tuple(e for e in eligibility if e.id == command.eligibility_id)
    if len(matches) != 1:
        raise ValidationError("Gizmo requires one GM-approved eligibility decision")
    approved = matches[0]
    item = approved.item
    if (
        approved.actor_id != command.actor_id
        or approved.session_id != command.session_id
        or item.owner_id != command.actor_id
        or not approved.pocket_sized
        or not approved.could_have_carried
        or approved.gadgeteer_invention
        or item.quantity != 1
        or item.container_id is not None
        or item.ground is not None
        or item.equipped
        or item.ready
    ):
        raise ValidationError("Gizmo must be eligible pocket-sized undeclared equipment")
    criteria = {
        "owned": approved.owned_but_undeclared,
        "character-concept": approved.probably_owned
        and approved.matches_character_concept
        and approved.minor_or_ignorable,
        "common-device": approved.inexpensive and approved.widely_available_at_actor_tl,
    }
    if not criteria[approved.category]:
        raise ValidationError("Gizmo fails its source-defined eligibility category")
    if any(e.eligibility_id == approved.id for e in events) or any(
        i.id == item.id for i in state.items + state.expended_items
    ):
        raise ConflictError("Gizmo instance or eligibility has already entered play")
    outcome = GizmoOutcome(
        command_id=command.id,
        actor_id=command.actor_id,
        session_id=command.session_id,
        eligibility_id=approved.id,
        item_id=item.id,
        uses_remaining=levels - spent - 1,
    )
    updated = _commit(state, command, outcome, state.items + (item,))
    engine.validate(updated)
    return updated, outcome
