"""Actor-scoped B27 memory and explicit recovery of unattributed legacy rows.

A trusted host can restore missing attribution through an immutable original
command/receipt join, or record a GM decision separately. Neither path changes
old event bytes or silently replaces an already-made recognition check.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import Field

from wayfarer.engine.rules.social.social_hooks import RecognitionRoll
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialOutcome,
    _known_recognition,
    _social_identity,
)
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

ATTRIBUTION_PREFIX = "social:recognition-attribution:"


class LegacyRecognitionAttribution(Record):
    """A trusted GM identifies whose old encounter a specific recorded row describes."""

    event_id: Id
    actor_id: Id
    reason: str = Field(min_length=1, max_length=1000)


class _AttributionRecord(Record):
    command_id: Id
    attribution: LegacyRecognitionAttribution
    source_digest: str


def _attribution_id(event_id: str) -> str:
    return ATTRIBUTION_PREFIX + hashlib.sha256(event_id.encode()).hexdigest()


def _source_digest(event: ResourceEvent) -> str:
    return hashlib.sha256(event.model_dump_json().encode()).hexdigest()


def _adjudicated_recognition_actor(state: ResourceState, event: ResourceEvent) -> str | None:
    marker = next((row for row in state.events if row.id == _attribution_id(event.id)), None)
    if marker is None:
        return None
    record = _AttributionRecord.model_validate(
        json.loads(marker.kind)["private"]["legacy_recognition"]
    )
    if (
        record.attribution.event_id != event.id
        or record.source_digest != _source_digest(event)
        or marker.target_id != event.target_id
        or not any(
            receipt.command_id == record.command_id
            and receipt.digest == hashlib.sha256(record.model_dump_json().encode()).hexdigest()
            for receipt in state.receipts
        )
    ):
        raise ValidationError("Legacy recognition attribution no longer matches its source event")
    return record.attribution.actor_id


def _attributed_recognition_actor(
    state: ResourceState, event: ResourceEvent, sources: tuple[SocialCommand, ...]
) -> str | None:
    candidates = {
        actor_id
        for actor_id in (
            _recognition_actor(event),
            _recorded_recognition_actor(state, event, sources),
            _adjudicated_recognition_actor(state, event),
        )
        if actor_id is not None
    }
    if len(candidates) > 1:
        raise ValidationError("Legacy recognition attribution conflicts with its immutable source")
    return next(iter(candidates), None)


def _recognition_actor(event: ResourceEvent) -> str | None:
    """Attribute new receipts, or an old Influence contest, to its actual actor.

    Legacy plain reaction receipts did not record the actor. Reputation IDs are
    shared by purchases on different characters, so an unattributed row cannot
    establish recognition for a prepared reaction's actor.
    """
    try:
        private = json.loads(event.kind)["private"]
        if "recognition_actor_id" in private:
            actor_id = private["recognition_actor_id"]
        else:
            contest = private.get("contest") or private.get("influence", {}).get("contest")
            actor_id = contest["first_id"]
    except json.JSONDecodeError, KeyError, TypeError, AttributeError:
        return None
    return actor_id if isinstance(actor_id, str) else None


def _recorded_recognition_actor(
    state: ResourceState, event: ResourceEvent, sources: tuple[SocialCommand, ...]
) -> str | None:
    """Join exact immutable outer commands through their existing receipt digest."""
    actors = set()
    for source in sources:
        digest, event_id, legacy_id = _social_identity(source)
        if source.subject_id != event.target_id or event.id not in (event_id, legacy_id):
            continue
        if not any(
            receipt.command_id == source.id and receipt.digest == digest
            for receipt in state.receipts
        ):
            raise ValidationError("Recognition source command does not match its committed receipt")
        actors.add(source.actor_id)
    if len(actors) > 1:
        raise ValidationError("Legacy recognition source has ambiguous actor provenance")
    return next(iter(actors), None)


def _remembered_recognition(
    state: ResourceState,
    actor_id: str,
    subject_id: str,
    reputation_ids: set[str],
    sources: tuple[SocialCommand, ...],
) -> dict[str, RecognitionRoll]:
    attributed = []
    ambiguous = set()
    for event in state.events:
        if not event.id.startswith(("social:", "social-key:")) or event.target_id != subject_id:
            continue
        remembered = _known_recognition(state.model_copy(update={"events": (event,)}), subject_id)
        if not reputation_ids.intersection(remembered):
            continue
        event_actor = _attributed_recognition_actor(state, event, sources)
        if event_actor is None:
            ambiguous.update(reputation_ids.intersection(remembered))
        elif event_actor == actor_id:
            attributed.append(event)
    known = _known_recognition(state.model_copy(update={"events": tuple(attributed)}), subject_id)
    if ambiguous.difference(known):
        raise ValidationError(
            "Legacy recognition requires its recorded source command before reuse"
        )
    return known


def _validate_recognition(roll: RecognitionRoll) -> None:
    if (
        not roll.reputation_id
        or len(roll.dice) != 3
        or any(type(die) is not int or not 1 <= die <= 6 for die in roll.dice)
        or type(roll.total) is not int
        or roll.total != sum(roll.dice)
        or type(roll.target) is not int
        or roll.target not in (7, 10)
        or type(roll.recognized) is not bool
        or roll.recognized != (roll.total <= roll.target)
    ):
        raise ValidationError("Invalid remembered reaction recognition")


def attribute_legacy_recognition(
    state: ResourceState,
    world: World,
    command_id: str,
    attribution: LegacyRecognitionAttribution,
    *,
    player_actor_ids: tuple[str, ...],
    system: bool = False,
) -> ResourceState:
    """Record a GM's bounded attribution without dice, disclosure or rewriting.

    The host supplies current trusted GM authority under its command lock. This
    is an explicit adjudication of missing identity, never a fresh check or a
    replacement recognition outcome. Conflicting later assignments are refused.
    """
    if not system:
        raise ValidationError("Legacy recognition attribution requires trusted GM authority")
    marker_id = _attribution_id(attribution.event_id)
    marker = next((event for event in state.events if event.id == marker_id), None)
    if marker is not None:
        recorded = _AttributionRecord.model_validate(
            json.loads(marker.kind)["private"]["legacy_recognition"]
        )
        digest = hashlib.sha256(recorded.model_dump_json().encode()).hexdigest()
        if (
            recorded.command_id == command_id
            and recorded.attribution == attribution
            and any(
                receipt.command_id == command_id and receipt.digest == digest
                for receipt in state.receipts
            )
        ):
            return state
        raise ConflictError("Legacy recognition already has an immutable attribution")
    if any(receipt.command_id == command_id for receipt in state.receipts):
        raise ConflictError("Legacy recognition command ID was already committed")
    source = next((event for event in state.events if event.id == attribution.event_id), None)
    if source is None or not source.id.startswith(("social:", "social-key:")):
        raise ValidationError("Legacy recognition source event is unavailable")
    if _recognition_actor(source) is not None:
        raise ValidationError("Recognition source already identifies its actor")
    rows = _known_recognition(state.model_copy(update={"events": (source,)}), source.target_id)
    if not rows:
        raise ValidationError("Legacy source event has no recorded recognition")
    for row in rows.values():
        _validate_recognition(row)
    world.validate()
    actors = {entity.id for entity in world.entities if entity.kind is EntityKind.ACTOR}
    if (
        not {attribution.actor_id, source.target_id} <= actors
        or attribution.actor_id == source.target_id
    ):
        raise ValidationError("Legacy recognition requires distinct world actor and NPC identities")
    if source.target_id in player_actor_ids:
        raise ValidationError("Legacy recognition observer must be an NPC")
    record = _AttributionRecord(
        command_id=command_id, attribution=attribution, source_digest=_source_digest(source)
    )
    digest = hashlib.sha256(record.model_dump_json().encode()).hexdigest()
    event = ResourceEvent(
        id=marker_id,
        at=state.game_time,
        target_id=source.target_id,
        kind=json.dumps(
            {
                "public": SocialOutcome(
                    kind="reaction", outcome="recognition-recorded"
                ).model_dump_json(),
                "private": {"legacy_recognition": record.model_dump(mode="json")},
            }
        ),
    )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "events": state.events + (event,),
            "receipts": state.receipts + (Receipt(command_id=command_id, digest=digest),),
        }
    )
