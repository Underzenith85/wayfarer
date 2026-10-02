"""Private source, declaration and captured-context records for composed attacks."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy
from wayfarer.engine.simulation.resources import Command
from wayfarer.engine.simulation.traits.composed_attacks import AttackCompositionContext
from wayfarer.models import Id, Record


class BindComposedSource(Command):
    kind: Literal["bind"] = "bind"
    purchase_id: Literal["advantage:innate-attack"] = "advantage:innate-attack"
    description: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    specialty: Literal["beam", "breath", "gaze", "projectile"]
    emitter_limb: Literal["left-arm", "right-arm"] | None = None
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    incubation_seconds: int = Field(default=86400, ge=1, le=31536000)
    cyclic_policy: CyclicPolicy | None = None


class ComposedSource(Record):
    id: Id
    campaign_id: Id
    actor_id: Id
    purchase_id: Literal["advantage:innate-attack"] = "advantage:innate-attack"
    build_revision: str
    source_revision: str
    declared_by: Id
    description: str
    specialty: Literal["beam", "breath", "gaze", "projectile"]
    emitter_limb: Literal["left-arm", "right-arm"] | None = None
    damage_type: DamageType
    damage_dice: int = Field(ge=1)
    profile: AttackProfile
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    incubation_seconds: int = Field(ge=1)
    cyclic_policy: CyclicPolicy | None = None


class ComposedPending(Record):
    id: Id
    command_id: Id
    campaign_id: Id
    encounter_id: Id
    pending_id: Id
    attacker_id: Id
    target_id: Id
    source: ComposedSource
    stage: Literal["defense", "resistance"]
    opened_round: int
    opened_turn: int


class UseComposedAttack(Command):
    kind: Literal["declare", "aim"]
    encounter_id: Id
    source_id: Id
    target_id: Id


class ResistComposedAttack(Command):
    kind: Literal["resist"] = "resist"
    encounter_id: Id
    pending_id: Id
    resist: bool


class AbandonComposedAttack(Command):
    kind: Literal["abandon"] = "abandon"
    encounter_id: Id
    pending_id: Id


class ContinueComposedCritical(Command):
    kind: Literal["continue-critical"] = "continue-critical"
    encounter_id: Id
    pending_id: Id
    critical_id: Id
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_id: Id
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    effect: Literal["lose-balance", "disable-source"]
    duration_seconds: int | None = Field(default=None, ge=1, le=31536000)


ComposedCommand = (
    BindComposedSource
    | UseComposedAttack
    | ResistComposedAttack
    | AbandonComposedAttack
    | ContinueComposedCritical
)
ADAPTER: TypeAdapter[ComposedCommand] = TypeAdapter(
    Annotated[ComposedCommand, Field(discriminator="kind")]
)


@dataclass(frozen=True)
class CurrentAttack:
    source: ComposedSource
    attacker: ValidatedBuild
    target: ValidatedBuild
    context: AttackCompositionContext
    resistance: int
    speed: float
    size: int
    visibility_penalty: int
