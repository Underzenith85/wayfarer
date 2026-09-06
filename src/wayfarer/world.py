"""Canonical world state, actor perspectives and narrative commitments."""

from dataclasses import dataclass, replace
from enum import StrEnum

from wayfarer.errors import ValidationError


class EntityKind(StrEnum):
    ACTOR = "actor"
    LOCATION = "location"
    FACTION = "faction"
    OBJECT = "object"


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    kind: EntityKind
    name: str
    location_id: str | None = None
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class Connection:
    source_id: str
    destination_id: str
    label: str


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    subject_id: str
    predicate: str
    value: str


@dataclass(frozen=True, slots=True)
class Belief:
    actor_id: str
    fact_id: str
    value: str


class CommitmentKind(StrEnum):
    PROMISE = "promise"
    THREAT = "threat"
    DEBT = "debt"
    OBJECTIVE = "objective"


class CommitmentStatus(StrEnum):
    HIDDEN = "hidden"
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Commitment:
    id: str
    kind: CommitmentKind
    debtor_id: str
    creditor_id: str | None
    description: str
    reveal_fact_id: str | None = None
    complete_fact_id: str | None = None
    status: CommitmentStatus = CommitmentStatus.HIDDEN


@dataclass(frozen=True, slots=True)
class Perspective:
    entities: tuple[Entity, ...]
    facts: tuple[Fact, ...]
    beliefs: tuple[Belief, ...]
    commitments: tuple[Commitment, ...]


@dataclass(frozen=True, slots=True)
class World:
    entities: tuple[Entity, ...] = ()
    connections: tuple[Connection, ...] = ()
    facts: tuple[Fact, ...] = ()
    knowledge: tuple[tuple[str, str], ...] = ()
    beliefs: tuple[Belief, ...] = ()
    commitments: tuple[Commitment, ...] = ()

    def validate(self) -> None:
        entity_ids = {entity.id for entity in self.entities}
        fact_ids = {fact.id for fact in self.facts}
        if len(entity_ids) != len(self.entities) or len(fact_ids) != len(self.facts):
            raise ValidationError("Duplicate stable ID")
        refs = [(e.id, e.location_id) for e in self.entities] + [
            (e.id, e.owner_id) for e in self.entities
        ]
        refs += [(c.source_id, c.destination_id) for c in self.connections]
        refs += [(f.id, f.subject_id) for f in self.facts]
        if any(ref is not None and ref not in entity_ids for _, ref in refs):
            raise ValidationError("Invalid entity reference")
        if any(actor not in entity_ids or fact not in fact_ids for actor, fact in self.knowledge):
            raise ValidationError("Invalid knowledge reference")
        if any(b.actor_id not in entity_ids or b.fact_id not in fact_ids for b in self.beliefs):
            raise ValidationError("Invalid belief reference")
        for c in self.commitments:
            if c.debtor_id not in entity_ids or (
                c.creditor_id is not None and c.creditor_id not in entity_ids
            ):
                raise ValidationError("Invalid commitment entity reference")
            if any(
                f is not None and f not in fact_ids for f in (c.reveal_fact_id, c.complete_fact_id)
            ):
                raise ValidationError("Invalid commitment fact reference")

    def perspective(self, actor_id: str) -> Perspective:
        self.validate()
        known = {fact for actor, fact in self.knowledge if actor == actor_id}
        facts = tuple(fact for fact in self.facts if fact.id in known)
        beliefs = tuple(belief for belief in self.beliefs if belief.actor_id == actor_id)
        commitments = tuple(
            c
            for c in self.commitments
            if c.status is not CommitmentStatus.HIDDEN or c.reveal_fact_id in known
        )
        visible_ids = (
            {actor_id}
            | {fact.subject_id for fact in facts}
            | {c.debtor_id for c in commitments}
            | {c.creditor_id for c in commitments if c.creditor_id}
        )
        return Perspective(
            tuple(e for e in self.entities if e.id in visible_ids), facts, beliefs, commitments
        )

    def learn(self, actor_id: str, fact_id: str) -> "World":
        updated = replace(
            self, knowledge=tuple(sorted(set(self.knowledge) | {(actor_id, fact_id)}))
        )
        updated.validate()
        commitments = tuple(
            replace(
                c,
                status=CommitmentStatus.COMPLETED
                if c.complete_fact_id == fact_id
                else CommitmentStatus.ACTIVE
                if c.reveal_fact_id == fact_id
                else c.status,
            )
            for c in updated.commitments
        )
        return replace(updated, commitments=commitments)


@dataclass(frozen=True, slots=True)
class KnowledgeRevealed:
    """Immutable persistence payload; facts are referenced, never duplicated."""

    actor_id: str
    fact_id: str


def replay(seed: World, events: tuple[KnowledgeRevealed, ...]) -> World:
    state = seed
    for event in events:
        state = state.learn(event.actor_id, event.fact_id)
    return state
