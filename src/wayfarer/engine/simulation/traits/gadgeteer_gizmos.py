"""B58 small inventions: actual materials, secret skill roll and backfire injury."""

import hashlib
from collections.abc import Mapping

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.character.traits.gadgeteering import capability
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.engine.simulation.traits.gizmos import (
    GizmoOutcome,
    RevealGizmo,
    _commit,
    _prior,
    history,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE = "gurps-basic-set-4e-2004"


class GizmoMaterial(Record):
    item_id: Id
    quantity: int = Field(ge=1)


class GadgeteerGizmoApproval(Record):
    """GM-authored source facts; none are player command fields.

    B58 has no universal materials formula or backfire damage expression. The
    selected invention, materials and backfire consequence require explicit GM
    approval. Existing inventions retain their real ID and condition.
    """

    id: Id
    actor_id: Id
    session_id: Id
    item: Item
    small_invention: bool
    own_invention: bool
    build_on_spot: bool = False
    materials: tuple[GizmoMaterial, ...] = ()
    required_skill_ids: tuple[Id, ...] = ()
    relevant_skill_id: Id | None = None
    skill_modifier: int = Field(default=-2, le=-2)
    backfire_damage: int = Field(ge=1)
    backfire_resistance: int = Field(default=0, ge=0)


class SecretGizmoRoll(Record):
    command_id: Id
    check: CheckTrace | None = None
    hp_lost: int = Field(default=0, ge=0)


class GizmoPrivateState(Record):
    """GM-only snapshot, saved atomically with resources; never player projected."""

    rolls: tuple[SecretGizmoRoll, ...] = ()


def gm_gizmo_rolls(
    state: GizmoPrivateState, *, gm_authorized: bool = False
) -> tuple[SecretGizmoRoll, ...]:
    if not gm_authorized:
        raise ValidationError("Secret Gizmo rolls require GM visibility")
    return state.rolls


def _materials(state: ResourceState, approved: GadgeteerGizmoApproval) -> tuple[Item, ...]:
    wanted = {m.item_id: m.quantity for m in approved.materials}
    if len(wanted) != len(approved.materials) or not wanted:
        raise ValidationError("Crafted Gizmo requires distinct actual materials")
    found = {item.id: item for item in state.items}
    for identity, quantity in wanted.items():
        item = found.get(identity)
        if (
            item is None
            or item.owner_id != approved.actor_id
            or item.ground is not None
            or item.equipped
            or item.ready
            or item.container_id is not None
            or item.quantity < quantity
            or (item.condition is not None and item.condition.disabled)
        ):
            raise ValidationError("Gizmo material is unavailable or outside actor custody")
    return tuple(
        item.model_copy(update={"quantity": item.quantity - wanted[item.id]})
        if item.id in wanted
        else item
        for item in state.items
        if item.id not in wanted or item.quantity > wanted[item.id]
    )


def _preflight(
    engine: ResourceEngine,
    state: ResourceState,
    command: RevealGizmo,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    approved: GadgeteerGizmoApproval,
) -> tuple[int, int, tuple[Item, ...]]:
    events = history(state)
    sessions = tuple(e.session_id for e in events if e.item_id is None)
    if not sessions or sessions[-1] != command.session_id:
        raise ValidationError("Gizmo requires the current GM-opened session")
    levels = sum(
        e.levels
        for e in mundane_trait_effects(build, definitions)
        if e.definition_id == "trait:advantage:gizmos"
    )
    spent = sum(
        e.item_id is not None
        and e.actor_id == command.actor_id
        and e.session_id == command.session_id
        for e in events
    )
    if capability(build) is None or not 1 <= levels <= 3 or spent >= levels:
        raise ValidationError("Gadgeteer Gizmo requires approved purchases and an available use")
    item = approved.item
    if (
        approved.id != command.eligibility_id
        or approved.actor_id != command.actor_id
        or approved.session_id != command.session_id
        or not approved.small_invention
        or not approved.own_invention
        or item.owner_id != command.actor_id
        or item.quantity != 1
        or item.ground is not None
        or item.container_id is not None
        or item.equipped
        or item.ready
    ):
        raise ValidationError("Gizmo is not an approved small owned invention")
    if any(i.id == item.id for i in state.items + state.expended_items):
        raise ConflictError("Gizmo invention instance has already entered play")
    if any(e.eligibility_id == approved.id for e in events):
        raise ConflictError("Gizmo approval has already been used")
    engine.validate(state.model_copy(update={"items": state.items + (item,)}))
    if not approved.build_on_spot:
        return levels - spent - 1, 0, state.items
    targets = {v.target: int(v.value) for v in build.sheet.values}
    known = {p.definition_id for p in build.purchases if p.amount > 0}
    required = set(approved.required_skill_ids)
    if (
        not required
        or approved.relevant_skill_id not in required
        or not required <= known
        or not required <= targets.keys()
    ):
        raise ValidationError("Crafted Gizmo requires learned relevant and prerequisite skills")
    spec = engine.specs[item.definition_id]
    if spec.durability is None:
        raise ValidationError("Crafted Gizmo needs actual object condition support")
    hp = next((p for p in state.pools if p.id == "hp:" + command.actor_id), None)
    if hp is None or hp.injury is None or "attribute:ht" not in targets:
        raise ValidationError("Gizmo backfire requires canonical actor health")
    return (
        levels - spent - 1,
        targets[approved.relevant_skill_id] + approved.skill_modifier,
        _materials(state, approved),
    )


def craft_gizmo(
    engine: ResourceEngine,
    resources: ResourceState,
    private: GizmoPrivateState,
    command: RevealGizmo,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    approval: GadgeteerGizmoApproval,
    *,
    rng: RandomSource,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, GizmoPrivateState, GizmoOutcome]:
    """The host commits both snapshots together; failure still spends one Gizmo."""
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Gadgeteer Gizmo requires actor and GM authority")
    prior = _prior(resources, command)
    if prior is not None:
        if not any(r.command_id == command.id for r in private.rolls):
            raise ConflictError("Gizmo private receipt is missing")
        return resources, private, prior
    remaining, target, materials = _preflight(
        engine, resources, command, build, definitions, approval
    )
    check = success_roll(PROFILE, target, rng=rng) if approval.build_on_spot else None
    item = approval.item
    hp_lost = 0
    if check is not None and not check.outcome.succeeded:
        spec = engine.specs[item.definition_id]
        assert spec.durability is not None
        item = item.model_copy(
            update={"condition": ObjectCondition(hp=spec.durability.hp, disabled=True)}
        )
        if check.outcome is Outcome.CRITICAL_FAILURE:
            ht = next(int(v.value) for v in build.sheet.values if v.target == "attribute:ht")
            resources, injury = apply_injury(
                resources,
                Wound(
                    id="gizmo-backfire:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                    basic_damage=approval.backfire_damage,
                    resistance=approval.backfire_resistance,
                    damage_type="cr",
                    injury_source="internal",
                ),
                ht=ht,
                rng=rng,
                system=True,
            )
            hp_lost = injury.injury
    outcome = GizmoOutcome(
        command_id=command.id,
        actor_id=command.actor_id,
        session_id=command.session_id,
        eligibility_id=approval.id,
        item_id=item.id,
        uses_remaining=remaining,
    )
    updated = _commit(resources, command, outcome, materials + (item,))
    engine.validate(updated)
    record = SecretGizmoRoll(command_id=command.id, check=check, hp_lost=hp_lost)
    return updated, private.model_copy(update={"rolls": private.rolls + (record,)}), outcome
