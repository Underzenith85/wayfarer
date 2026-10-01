"""Private approved attack descriptions and immutable pending uses (B61-62).

The source is an approved purchase plus GM-authored description facts. It never
contains caller-selected skill, defense, damage, protection, range or resistance.
"""

from __future__ import annotations

import hashlib
from typing import Literal, cast

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.rules.traits.cyclic import cyclic_profile
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE = "gurps-basic-set-4e-2004"
SOURCE_PREFIX = "composed-source:"
PENDING_PREFIX = "composed-pending:"
FINISHED_PREFIX = "composed-finished:"


def identity(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()


def source_id(actor_id: str, purchase_id: str = "advantage:innate-attack") -> str:
    # A new command, target, description or build revision is not a new Symptoms cause.
    return identity(SOURCE_PREFIX, actor_id + ":" + purchase_id)


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


def raw_build(runtime: RulesContext, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Composed attack requires an approved actor")
    compiled, _ = runtime.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    if compiled.statistics is None or compiled.statistics.profile_id != PROFILE:
        raise ValidationError("Composed attack requires the exact Basic Set profile")
    return compiled


def source_history(resources: ResourceState) -> tuple[ComposedSource, ...]:
    return tuple(
        ComposedSource.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(SOURCE_PREFIX)
    )


def bind(
    runtime: RulesContext, state: PlayState, command: BindComposedSource, principal_id: str
) -> tuple[PlayState, ComposedSource]:
    compiled = raw_build(runtime, state, command.actor_id)
    purchase = next(
        (p for p in compiled.trait_purchases if p.definition_id == command.purchase_id), None
    )
    traits = attack_defense_traits(compiled, runtime.reviewer.compiler.definitions)
    damage_type = traits.natural_damage_type(command.purchase_id)
    if purchase is None or purchase.trait is None or damage_type is None:
        raise ValidationError("Source requires the current approved Innate Attack purchase")
    if purchase.trait.modifiers:
        raise ValidationError(
            "Legacy Innate Attack modifiers require a separately supported source consumer"
        )
    if damage_type == "cor":
        raise ValidationError(
            "Corrosion requires the unsupported B379 persistent DR degradation consumer"
        )
    if damage_type in ("cr", "cut"):
        raise ValidationError(
            "Crushing and cutting require the unsupported B378 knockback continuation consumer"
        )
    profile = cyclic_profile(purchase.trait.attack_modifiers, damage_type)
    if command.emitter_limb is not None and command.specialty not in ("beam", "projectile"):
        raise ValidationError("Breath and Gaze do not establish an emitting arm")
    if profile.cyclic_interval_seconds is not None:
        if (
            command.cyclic_policy is None
            or command.cyclic_policy.condition != profile.cyclic_stop_condition
        ):
            raise ValidationError("Cyclic source requires its approved stopping procedure")
    elif command.cyclic_policy is not None:
        raise ValidationError("A non-Cyclic source cannot bind a Cyclic procedure")
    if profile.contagious != "none" and command.contagion_vector is None:
        raise ValidationError("Contagious source requires a GM-authored illness vector")
    if profile.contagious == "none" and command.contagion_vector is not None:
        raise ValidationError("An illness vector requires the approved Contagious modifier")
    description = command.model_dump_json(exclude={"id", "expected_revision"})
    source = ComposedSource(
        id=source_id(command.actor_id, command.purchase_id),
        campaign_id=state.campaign_id,
        actor_id=command.actor_id,
        build_revision=compiled.revision,
        source_revision=identity("revision:", compiled.revision + description),
        declared_by=principal_id,
        description=command.description,
        specialty=command.specialty,
        emitter_limb=command.emitter_limb,
        damage_type=cast(DamageType, damage_type),
        damage_dice=purchase.amount,
        profile=profile,
        contagion_vector=command.contagion_vector,
        incubation_seconds=command.incubation_seconds,
        cyclic_policy=command.cyclic_policy,
    )
    event = ResourceEvent(
        id=identity(SOURCE_PREFIX, command.id),
        at=state.resources.game_time,
        target_id=command.actor_id,
        kind=source.model_dump_json(),
    )
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    ), source


def current_source(
    runtime: RulesContext, state: PlayState, actor_id: str, identifier: str
) -> tuple[ComposedSource, ValidatedBuild]:
    source = next(
        (s for s in reversed(source_history(state.resources)) if s.id == identifier), None
    )
    if source is None or source.campaign_id != state.campaign_id or source.actor_id != actor_id:
        raise ValidationError("Unknown approved composed attack source")
    compiled = raw_build(runtime, state, actor_id)
    if compiled.revision != source.build_revision:
        raise ConflictError("Composed source approval changed; bind the approved description again")
    return source, compiled


def pending_binding(
    resources: ResourceState, encounter: Encounter, pending: PendingDefense
) -> ComposedPending:
    identifier = pending.composed_attack_id
    matches = tuple(e for e in resources.events if e.id == identifier)
    if identifier is None or len(matches) != 1 or not identifier.startswith(PENDING_PREFIX):
        raise ValidationError("Pending composed attack has no unique private source binding")
    bound = ComposedPending.model_validate_json(matches[0].kind)
    if (
        (
            bound.id,
            bound.encounter_id,
            bound.pending_id,
            bound.attacker_id,
            bound.target_id,
            bound.source.id,
            bound.opened_round,
            bound.opened_turn,
        )
        != (
            identifier,
            encounter.id,
            pending.id,
            pending.attacker_id,
            pending.defender_id,
            pending.weapon_id,
            pending.opened_round,
            pending.opened_turn,
        )
        or bound.campaign_id != bound.source.campaign_id
        or bound.attacker_id != bound.source.actor_id
        or pending.spell_cast_id is not None
        or pending.suppression_zone_id is not None
        or pending.shield_rush
        or pending.spray_targets
        or pending.shots != 1
        or (bound.stage == "defense") != (bound.source.profile.malediction_range == "none")
        or (bound.stage == "resistance" and pending.allowed != ("none",))
        or not set(pending.allowed) <= {"none", "dodge"}
        or pending.cover_item_id is not None
        or pending.area_aim_point is not None
        or pending.overpenetration_target_id is not None
        or pending.armor_chink
        or pending.mode_id is not None
        or pending.hit_location is not None
        or pending.target_item_id is not None
        or pending.protected_defender_id is not None
        or any(e.id == identity(FINISHED_PREFIX, bound.id) for e in resources.events)
    ):
        raise ValidationError("Pending composed attack source or stage does not match")
    return bound


def finish_binding(
    resources: ResourceState, binding: ComposedPending, reason: str
) -> ResourceState:
    event = ResourceEvent(
        id=identity(FINISHED_PREFIX, binding.id),
        at=resources.game_time,
        target_id=binding.attacker_id,
        kind=reason,
    )
    if any(e.id == event.id for e in resources.events):
        raise ConflictError("Composed attack has already finished")
    return resources.model_copy(update={"events": resources.events + (event,)})


def validate_composed_state(state: PlayState) -> None:
    """Bind current private pending identities to the enclosing campaign checkpoint."""
    for encounter in state.encounters:
        pending = encounter.pending_defense
        if pending is not None and pending.composed_attack_id is not None:
            bound = pending_binding(state.resources, encounter, pending)
            if bound.campaign_id != state.campaign_id:
                raise ValidationError("Composed pending campaign identity does not match")
