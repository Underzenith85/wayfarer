"""Private, directed nonvisual evidence; geometry never grants sensory knowledge.

The checkpoint must run around every committed transition. Its durable tombstones
prevent movement, recovery or build changes from resurrecting old observations
when an actor later returns to the same coordinates or build. The read path also
checks live scope, so a transition awaiting its checkpoint already fails closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.health.symptom_state import active
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.encounter import Encounter

EVENT_PREFIX = "combat-sense:"
INVALIDATION_PREFIX = "combat-sense-invalidated:"
Explanation = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
NonvisualBasis = Literal["sound", "touch", "vibration", "smell", "other-nonvisual"]


class ExactLocation(Record):
    """A separate B394 certainty justification, never an ordinary Hearing success."""

    basis: Literal["continuous-contact", "constrained-location", "precise-nonvisual-sense"]
    explanation: Explanation


class NonvisualObservation(Record):
    basis: NonvisualBasis
    explanation: Explanation
    located: bool = False
    # Nonempty supporting fact means awareness of this target's attack, not presence.
    attack_awareness: Explanation | None = None
    exact_location: ExactLocation | None = None

    @model_validator(mode="after")
    def coherent(self) -> NonvisualObservation:
        if self.exact_location is not None and not self.located:
            raise ValueError("Exact location requires a located target")
        if not self.located and self.attack_awareness is None:
            raise ValueError("An observation requires location or attack awareness")
        return self


class CombatSensoryEvidence(Record):
    version: Literal[1] = 1
    id: Id
    command_id: Id
    campaign_id: Id
    encounter_id: Id
    observer_id: Id
    target_id: Id
    purpose: Literal["combat-nonvisual-location"] = "combat-nonvisual-location"
    basis: NonvisualBasis
    explanation: Explanation
    located: bool
    aware_of_attack: bool
    attack_awareness: Explanation | None = None
    exact_location: ExactLocation | None = None
    hearing: CheckTrace | None = None
    awareness_source_id: Id | None = None
    declared_by: Id
    declared_revision: int = Field(ge=0)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent(self) -> CombatSensoryEvidence:
        if self.observer_id == self.target_id:
            raise ValueError("Sensory evidence requires distinct combatants")
        if self.aware_of_attack != (self.attack_awareness is not None):
            raise ValueError("Attack awareness requires its supporting fact")
        if self.awareness_source_id is not None and not self.aware_of_attack:
            raise ValueError("Inherited awareness requires an awareness fact")
        if self.exact_location is not None and not self.located:
            raise ValueError("Exact location requires a located target")
        if self.hearing is not None and (
            self.basis != "sound"
            or self.exact_location is not None
            or self.located != self.hearing.outcome.succeeded
        ):
            raise ValueError("Hearing supports only successful ordinary location")
        return self


class SensoryInvalidation(Record):
    evidence_id: Id
    invalidated_revision: int = Field(ge=0)
    reason: Literal["scope-changed", "superseded", "gm-revoked"]


def event_id(command_id: str) -> str:
    return EVENT_PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[CombatSensoryEvidence, ...]:
    return tuple(
        CombatSensoryEvidence.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(EVENT_PREFIX)
    )


def invalidations(resources: ResourceState) -> tuple[SensoryInvalidation, ...]:
    return tuple(
        SensoryInvalidation.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(INVALIDATION_PREFIX)
    )


def scope_digest(state: PlayState, encounter: Encounter, observer_id: str, target_id: str) -> str:
    """Snapshot relevant authority and motion, never a source of detection itself."""
    pair = {observer_id, target_id}
    if (
        len(pair) != 2
        or encounter.status != "active"
        or not pair <= {p.actor_id for p in encounter.participants}
        or not pair <= {a.actor_id for a in state.actors}
        or not any(e.id == encounter.id and e.status == "active" for e in state.encounters)
    ):
        raise ValidationError("Sensory evidence requires a current encounter and directed pair")
    spatial = encounter.spatial
    spatial_scope = (
        {
            "kind": "basic",
            # History, including invalidated facts, catches Basic move-and-return.
            "facts": [
                f.model_dump(mode="json")
                for f in spatial.facts
                if pair & {f.subject_id, f.object_id}
            ],
        }
        if isinstance(spatial, BasicSpatialContext)
        else {
            "kind": spatial.kind,
            "battlefield": spatial.battlefield_id,
            "placements": [
                p.model_dump(mode="json", exclude={"facing"})
                for p in spatial.placements
                if p.actor_id in pair
            ],
        }
    )
    scope = {
        "campaign": state.campaign_id,
        "encounter": encounter.id,
        "scene": encounter.scene_id,
        "pair": [observer_id, target_id],
        "spatial": spatial_scope,
        "actors": [
            {
                "id": actor.actor_id,
                "proposal": actor.proposal.model_dump(mode="json"),
                "approval": actor.approval.model_dump(mode="json") if actor.approval else None,
                "blind": actor.actor_id == observer_id
                and any(
                    effect.spec.kind == "blindness"
                    for effect in active(state.resources, actor.actor_id)
                ),
            }
            for actor in state.actors
            if actor.actor_id in pair
        ],
        "world_locations": [(e.id, e.location_id) for e in state.world.entities if e.id in pair],
    }
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evidence(
    state: PlayState, encounter: Encounter, observer_id: str, target_id: str
) -> CombatSensoryEvidence | None:
    """Latest valid proof for this directed pair; no fallback to older observations."""
    candidate = next(
        (
            entry
            for entry in reversed(history(state.resources))
            if (entry.campaign_id, entry.encounter_id, entry.observer_id, entry.target_id)
            == (state.campaign_id, encounter.id, observer_id, target_id)
        ),
        None,
    )
    if candidate is None or candidate.id in {i.evidence_id for i in invalidations(state.resources)}:
        return None
    try:
        canonical = next(e for e in state.encounters if e.id == encounter.id)
        matches = candidate.scope_digest == scope_digest(state, encounter, observer_id, target_id)
        matches = matches and candidate.scope_digest == scope_digest(
            state, canonical, observer_id, target_id
        )
    except StopIteration, ValidationError:
        return None
    return candidate if matches and candidate.declared_revision <= state.revision else None


def invalidate(
    resources: ResourceState,
    entry: CombatSensoryEvidence,
    revision: int,
    reason: Literal["scope-changed", "superseded", "gm-revoked"],
) -> ResourceState:
    if entry.id in {i.evidence_id for i in invalidations(resources)}:
        return resources
    invalidation = SensoryInvalidation(
        evidence_id=entry.id, invalidated_revision=revision, reason=reason
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=INVALIDATION_PREFIX + hashlib.sha256(entry.id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=entry.observer_id,
                    kind=invalidation.model_dump_json(),
                ),
            )
        }
    )


def invalidate_movement(
    resources: ResourceState,
    encounter_id: str,
    actor_ids: frozenset[str],
    *,
    revision: int,
) -> ResourceState:
    """Retire only this encounter's evidence after movement actually executes.

    Reducers may call this on their immutable candidate resources. Discarding a
    preview, rejected transition or unexecuted Wait suffix discards these events
    too; callers must never use a merely requested or validated path as motion.
    """
    if not actor_ids:
        return resources
    for entry in history(resources):
        if entry.encounter_id == encounter_id and actor_ids & {entry.observer_id, entry.target_id}:
            resources = invalidate(resources, entry, revision, "scope-changed")
    return resources


def checkpoint(
    state: PlayState, *, before: PlayState, moved_actor_ids: frozenset[str] = frozenset()
) -> PlayState:
    """Tombstone changed scope without a revision increment or random draws.

    A host that resolves an entire path before checkpointing must supply its
    actually moved actors, including paths ending back on the starting cell.
    Calling around each intermediate movement state is equally sufficient.
    """
    resources = state.resources
    invalid = {i.evidence_id for i in invalidations(resources)}
    for entry in history(resources):
        if entry.id in invalid:
            continue
        try:
            current = next(e for e in state.encounters if e.id == entry.encounter_id)
            changed = bool(moved_actor_ids & {entry.observer_id, entry.target_id})
            changed = changed or entry.scope_digest != scope_digest(
                state, current, entry.observer_id, entry.target_id
            )
            # Evidence just created in this transition has no earlier lifetime.
            if entry.declared_revision <= before.revision:
                previous = next(e for e in before.encounters if e.id == entry.encounter_id)
                changed = changed or entry.scope_digest != scope_digest(
                    before, previous, entry.observer_id, entry.target_id
                )
        except StopIteration, ValidationError:
            changed = True
        if changed:
            resources = invalidate(resources, entry, state.revision, "scope-changed")
    return (
        state.model_copy(update={"resources": resources}) if resources != state.resources else state
    )
