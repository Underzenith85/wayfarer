"""Authoritative transformation proposal, approval, treatment, and reversal service."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import ValidatedBuild, pool_limits
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build as actor_build
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.campaign.transformations import (
    AttachmentKind,
    AttachmentRoute,
    MorphMemory,
    TransformationRecord,
    TransformationRule,
    TransformationRules,
    current_body_id,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import Consume, Pool
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.harmful_physiology_play import reconcile_actor, settle_actor
from wayfarer.engine.simulation.traits.size_forms import require_native_size
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.orchestration.builds import canonical_build, spendable_points
from wayfarer.orchestration.fright_builds import refresh_checks
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class MemorizeMorph(Record):
    operation: Literal["memorize-start", "memorize-complete", "memorize-interrupt"]
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    rule_id: Id
    forget_rule_id: Id | None = None


class ProposeTransformation(Record):
    operation: Literal["propose"] = "propose"
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    expected_build_revision: str
    rule_id: Id


class ApproveTransformation(Record):
    operation: Literal["approve"] = "approve"
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    proposal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


class ResolveTransformation(Record):
    operation: Literal["resolve"] = "resolve"
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    proposal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolution: Literal["complete", "interrupt", "reverse", "force", "cure", "expire"]
    influence: str | None = Field(default=None, min_length=1)


def _route(rule: TransformationRule, kind: AttachmentKind) -> AttachmentRoute:
    return next(route for route in rule.attachment_routes if route.kind == kind)


def _rule(play: PlayService, actor_id: str, rule_id: str) -> TransformationRule:
    rules = play.engine.rules.transformations
    if rules is None:
        raise ValidationError("Character transformations are unavailable in this campaign")
    rule = next((entry for entry in rules.transformations if entry.id == rule_id), None)
    if rule is None or rule.actor_id != actor_id:
        raise ValidationError(
            "Transformation path was not authored and selected for this character"
        )
    return rule


def _target_build(
    play: PlayService, state: PlayState, rule: TransformationRule
) -> tuple[CharacterProposal, ValidatedBuild, ValidatedBuild]:
    require_native_size(state.resources, rule.actor_id)
    actor = next(a for a in state.actors if a.actor_id == rule.actor_id)
    if actor.approval is None:
        raise ValidationError("Transformation requires an approved source character")
    before = canonical_build(play, state, rule.actor_id)
    if (rule.target.name, rule.target.backstory) != (
        actor.proposal.draft.name,
        actor.proposal.draft.backstory,
    ):
        raise ValidationError("Transformation cannot rewrite character identity or history")
    proposal = actor.proposal.model_copy(update={"draft": rule.target})
    review = play.engine.reviewer.review(proposal)
    after = review.compilation.build
    if after is None:
        raise ValidationError("Transformation target is illegal under the pinned campaign rules")
    old = {purchase.definition_id: purchase for purchase in actor.proposal.draft.purchases}
    new = {purchase.definition_id: purchase for purchase in rule.target.purchases}
    routes = {route.definition_id: route.follows for route in rule.trait_routes}
    if routes.keys() != old.keys() | new.keys():
        raise ValidationError(
            "Transformation requires an explicit mapping for every old and new trait"
        )
    definitions = play.engine.reviewer.compiler.definitions
    for definition_id, follows in routes.items():
        definition = definitions.get(definition_id)
        if definition is None:
            raise ValidationError("Transformation mapping names an unknown campaign definition")
        if follows == "mind" and old.get(definition_id) != new.get(definition_id):
            raise ValidationError("Mind-following traits must be preserved exactly")
        if follows == "neither" and definition_id in new:
            raise ValidationError("A trait routed to neither mind nor body cannot remain purchased")
        if definition.kind is DefinitionKind.SKILL and follows != "mind":
            raise ValidationError("Mind transfer keeps skill points with the mind")
    for definition_id in ("attribute:st", "attribute:dx", "attribute:ht"):
        if definition_id in routes and rule.kind in ("mind-transfer", "death-transformation"):
            if routes[definition_id] != "body":
                raise ValidationError("A transferred body's ST, DX, and HT must follow the body")
    if (
        rule.kind in ("mind-transfer", "death-transformation")
        and routes.get("attribute:iq") != "mind"
    ):
        raise ValidationError("Mind transfer must preserve IQ with the mind")
    if rule.kind in ("alternate-form", "morph"):
        _validate_form_target(play, state, rule, before, after)
    if rule.kind in ("body-modification", "alternate-form", "morph"):
        changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if any(routes[key] != "body" for key in changed):
            raise ValidationError("Body modification may only alter body-routed traits")
    return proposal, before, after


def _validate_form_target(
    play: PlayService,
    state: PlayState,
    rule: TransformationRule,
    before: ValidatedBuild,
    after: ValidatedBuild,
) -> None:
    _require_form_concentration(state, rule.actor_id)
    _validate_shapeshifting(rule, before, after)
    if rule.kind == "alternate-form":
        _validate_alternate_form_count(play.engine.rules.transformations, rule, before)
    if rule.kind == "morph":
        _validate_morph_access(state, rule)


def _validate_alternate_form_count(
    rules: TransformationRules | None, rule: TransformationRule, before: ValidatedBuild
) -> None:
    assert rules is not None
    purchase = next(
        p for p in before.trait_purchases if p.definition_id == "advantage:alternate-form"
    )
    forms = tuple(
        r
        for r in rules.transformations
        if r.actor_id == rule.actor_id and r.kind == "alternate-form"
    )
    if len(forms) > purchase.amount:
        raise ValidationError("Authored alternate forms exceed the purchased number of forms")


def _validate_shapeshifting(
    rule: TransformationRule, before: ValidatedBuild, after: ValidatedBuild
) -> None:
    """Bind an authored form to the capability on the approved native build."""
    identifier = "advantage:" + rule.kind
    purchase = next((p for p in before.trait_purchases if p.definition_id == identifier), None)
    if purchase is None or purchase.trait is None:
        raise ValidationError("Shapeshifting requires the purchased approved form capability")
    if purchase.trait.modifiers:
        raise ValidationError("Shapeshifting modifiers require a separately implemented form path")
    parameters = dict(purchase.trait.parameters)
    if (
        parameters.get("native-template-cost") != rule.native_template_cost
        or not isinstance(parameters.get("target-template-cost"), int)
        or rule.target_template_cost is None
        or int(parameters["target-template-cost"]) < rule.target_template_cost
    ):
        raise ValidationError("Authored form exceeds or differs from the approved template limits")
    assert rule.native_template_cost is not None and rule.target_template_cost is not None
    if after.spent - before.spent != rule.target_template_cost - rule.native_template_cost:
        raise ValidationError("Authored build change disagrees with approved racial template costs")
    retained = next((p for p in after.trait_purchases if p.definition_id == identifier), None)
    if retained != purchase:
        raise ValidationError("An alternate build must retain its native shapeshifting capability")


def _cannot_concentrate(state: PlayState, actor_id: str) -> bool:
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    fp = next((p for p in state.resources.pools if p.id == "fp:" + actor_id), None)
    return bool(
        (hp and hp.injury and (hp.injury.unconscious or hp.injury.dead))
        or (
            fp
            and fp.fatigue
            and (fp.fatigue.unconscious or fp.fatigue.collapsed or fp.fatigue.heart_attack)
        )
    )


def _require_form_concentration(state: PlayState, actor_id: str) -> None:
    if _cannot_concentrate(state, actor_id):
        raise ValidationError("Shapeshifting requires a conscious actor able to concentrate")


def _validate_morph_access(state: PlayState, rule: TransformationRule) -> None:
    if any(
        memory.actor_id == rule.actor_id
        and memory.rule_id == rule.id
        and memory.status == "memorized"
        for memory in state.transformations.morph_memories
    ):
        return
    actor = next(e for e in state.world.entities if e.id == rule.actor_id)
    models = state.world.perspective(rule.actor_id).entities
    model = next((e for e in models if e.id == rule.form_model_id), None)
    if model is None or model.kind.value != "actor" or model.location_id != actor.location_id:
        raise ValidationError("Morph requires a visible present form model or a memorized form")


def _memorize_morph(play: PlayService, state: PlayState, command: MemorizeMorph) -> PlayState:
    rule = _rule(play, command.actor_id, command.rule_id)
    if rule.kind != "morph":
        raise ValidationError("Only Morph can memorize a form")
    active = next(
        (
            r
            for r in state.transformations.records
            if r.actor_id == command.actor_id and r.rule_id == rule.id and r.status == "active"
        ),
        None,
    )
    if active is None:
        raise ValidationError("Morph can memorize only the currently assumed form")
    _require_form_concentration(state, command.actor_id)
    build = canonical_build(play, state, command.actor_id)
    assert build.statistics is not None
    memories = state.transformations.morph_memories
    old = next(
        (m for m in memories if m.actor_id == command.actor_id and m.rule_id == rule.id), None
    )
    if command.operation == "memorize-start":
        if old is not None and old.status != "interrupted":
            raise ValidationError("Morph memory is already active")
        if any(m.actor_id == command.actor_id and m.status == "concentrating" for m in memories):
            raise ValidationError("Morph is already memorizing another form")
        retained = tuple(
            m for m in memories if m.actor_id == command.actor_id and m.status == "memorized"
        )
        if len(retained) >= build.statistics.iq:
            if command.forget_rule_id not in {m.rule_id for m in retained}:
                raise ValidationError(
                    "Full Morph memory requires selecting a memorized form to overwrite"
                )
            memories = tuple(
                m
                for m in memories
                if not (m.actor_id == command.actor_id and m.rule_id == command.forget_rule_id)
            )
        memory = MorphMemory(
            actor_id=command.actor_id,
            rule_id=rule.id,
            build_revision=build.revision,
            started_at=state.resources.game_time,
            ready_at=state.resources.game_time + 60,
            status="concentrating",
        )
    else:
        if old is None or old.status != "concentrating" or old.build_revision != build.revision:
            raise ValidationError("Morph is not memorizing this active form")
        if command.operation == "memorize-complete" and state.resources.game_time < old.ready_at:
            raise ValidationError("Morph memorization requires one minute of concentration")
        memory = old.model_copy(
            update={
                "status": "memorized" if command.operation == "memorize-complete" else "interrupted"
            }
        )
    memories = tuple(
        m for m in memories if not (m.actor_id == memory.actor_id and m.rule_id == memory.rule_id)
    ) + (memory,)
    return state.model_copy(
        update={
            "transformations": state.transformations.model_copy(update={"morph_memories": memories})
        }
    )


def _active(state: PlayState, proposal_id: str) -> TransformationRecord:
    record = next(
        (entry for entry in state.transformations.records if entry.proposal_id == proposal_id), None
    )
    if record is None:
        raise ValidationError("Unknown transformation proposal")
    return record


def _dead(state: PlayState, actor_id: str) -> bool:
    return actor_id in state.recovery.dead_actor_ids or any(
        p.id == "hp:" + actor_id and p.injury is not None and p.injury.dead
        for p in state.resources.pools
    )


def _validate_death_boundary(state: PlayState, rule: TransformationRule) -> None:
    if rule.requires_death != _dead(state, rule.actor_id):
        raise ValidationError("Transformation death prerequisite does not match state")


def _authority_state(
    state: PlayState, rule: TransformationRule, *, reverse: TransformationRecord | None = None
) -> PlayState:
    actor_id = rule.actor_id
    inventory = _route(rule, "inventory")
    credentials = _route(rule, "credentials")
    knowledge = _route(rule, "knowledge")
    control = _route(rule, "control")
    resources, world, members = state.resources, state.world, state.members
    if reverse is None:
        if inventory.follows != "mind":
            if inventory.destination_id not in {owner.actor_id for owner in resources.owners}:
                raise ValidationError("Inventory transformation route requires a resource owner")
            resources = resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(
                            update={
                                "owner_id": inventory.destination_id,
                                "equipped": False,
                                "ready": False,
                                "enchantments": tuple(
                                    binding.model_copy(
                                        update={"owner_id": inventory.destination_id}
                                    )
                                    for binding in item.enchantments
                                ),
                            }
                        )
                        if item.owner_id == actor_id
                        else item
                        for item in resources.items
                    )
                }
            )
        if credentials.follows != "mind":
            destination = credentials.destination_id
            if credentials.follows == "body" and destination not in {
                owner.actor_id for owner in resources.owners
            }:
                raise ValidationError("Credential transformation route requires a resource owner")
            resources = resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(
                            update={
                                "authorized_actor_ids": tuple(
                                    dict.fromkeys(
                                        (
                                            *(
                                                actor
                                                for actor in item.authorized_actor_ids
                                                if actor != actor_id
                                            ),
                                            *((destination,) if destination is not None else ()),
                                        )
                                    )
                                )
                            }
                        )
                        if actor_id in item.authorized_actor_ids
                        else item
                        for item in resources.items
                    )
                }
            )
        if knowledge.follows != "mind":
            retained = tuple(pair for pair in world.knowledge if pair[0] != actor_id)
            moved: tuple[tuple[str, str], ...] = ()
            if knowledge.follows == "body":
                destination = knowledge.destination_id
                assert destination is not None
                moved = tuple(
                    (destination, fact) for owner, fact in world.knowledge if owner == actor_id
                )
            world = type(world)(
                entities=world.entities,
                connections=world.connections,
                facts=world.facts,
                knowledge=tuple(sorted(set(retained + moved))),
                beliefs=world.beliefs,
                commitments=world.commitments,
            )
        if control.follows != "mind":
            members = tuple(
                member.model_copy(
                    update={"actor_ids": tuple(a for a in member.actor_ids if a != actor_id)}
                )
                for member in members
            )
            if control.follows == "body":
                if control.destination_id not in {
                    member.principal_id for member in members if member.role == "player"
                }:
                    raise ValidationError(
                        "Control transformation route requires a campaign principal"
                    )
                members = tuple(
                    member.model_copy(update={"actor_ids": member.actor_ids + (actor_id,)})
                    if member.principal_id == control.destination_id and member.role == "player"
                    else member
                    for member in members
                )
    else:
        # A mind-following attachment was never moved by the transformation.
        # Restoring its proposal-time snapshot would undo facts, trades, access
        # changes and control changes made while the other form was active.
        owners = dict(reverse.source_item_owners) if inventory.follows != "mind" else {}
        credentials_by_item = (
            dict(reverse.source_item_credentials) if credentials.follows != "mind" else {}
        )
        resources = resources.model_copy(
            update={
                "items": tuple(
                    item.model_copy(
                        update={
                            "owner_id": owners.get(item.id, item.owner_id),
                            "authorized_actor_ids": credentials_by_item.get(
                                item.id, item.authorized_actor_ids
                            ),
                            "enchantments": tuple(
                                binding.model_copy(
                                    update={"owner_id": owners.get(item.id, item.owner_id)}
                                )
                                for binding in item.enchantments
                            ),
                        }
                    )
                    if item.id in owners or item.id in credentials_by_item
                    else item
                    for item in resources.items
                )
            }
        )
        if knowledge.follows != "mind":
            knowledge_now = tuple(pair for pair in world.knowledge if pair[0] != actor_id)
            world = type(world)(
                entities=world.entities,
                connections=world.connections,
                facts=world.facts,
                knowledge=tuple(
                    sorted(
                        set(
                            knowledge_now
                            + tuple((actor_id, f) for f in reverse.source_knowledge_fact_ids)
                        )
                    )
                ),
                beliefs=world.beliefs,
                commitments=world.commitments,
            )
        if control.follows != "mind":
            members = tuple(
                member.model_copy(
                    update={
                        "actor_ids": tuple(a for a in member.actor_ids if a != actor_id)
                        + (
                            (actor_id,)
                            if member.principal_id in reverse.source_control_principal_ids
                            else ()
                        )
                    }
                )
                for member in members
            )
    return state.model_copy(update={"resources": resources, "world": world, "members": members})


def _scale_form_pool(pool: Pool, maximum: int) -> Pool:
    """B83 proportional loss, including restricted components of lost FP."""

    def scaled(value: int) -> int:
        return (value * maximum + pool.maximum - 1) // pool.maximum

    fatigue = pool.fatigue
    if fatigue is not None:
        starvation = scaled(fatigue.starvation)
        dehydration = scaled(fatigue.starvation + fatigue.dehydration) - starvation
        sleep = (
            scaled(fatigue.starvation + fatigue.dehydration + fatigue.sleep)
            - starvation
            - dehydration
        )
        fatigue = fatigue.model_copy(
            update={"starvation": starvation, "dehydration": dehydration, "sleep": sleep}
        )
    return pool.model_copy(
        update={
            "maximum": maximum,
            "current": maximum - scaled(pool.maximum - pool.current),
            "fatigue": fatigue,
        }
    )


def _apply_build(
    play: PlayService,
    state: PlayState,
    record: TransformationRecord,
    rule: TransformationRule,
    *,
    reverse: bool,
    command_id: str,
    revision: int | None = None,
) -> PlayState:
    state = settle_actor(play.rules_context, state, record.actor_id, command_id)
    proposal = record.source_proposal if reverse else record.target_proposal
    approval = record.source_approval if reverse else record.target_approval
    if approval is None:
        raise ValidationError("Transformation has no approved build")
    before = canonical_build(play, state, record.actor_id)
    review = play.engine.reviewer.review(proposal)
    after = review.compilation.build
    if after is None:
        raise ValidationError("Approved transformation build no longer compiles")
    maxima = pool_limits(after)
    body = record.source_body if reverse else rule.target_body

    def rebase(pool: Pool) -> Pool:
        if pool.id not in (f"hp:{record.actor_id}", f"fp:{record.actor_id}"):
            return pool
        maximum = maxima[pool.id.split(":", 1)[0]]
        if rule.kind in ("alternate-form", "morph"):
            pool = _scale_form_pool(pool, maximum)
        updated = _refreshed(
            pool,
            maxima[pool.id.split(":", 1)[0]],
            after,
            physical_traits(after, play.engine.reviewer.compiler.definitions),
        )
        if updated.injury is not None and body is not None:
            updated = updated.model_copy(
                update={
                    "injury": updated.injury.model_copy(
                        update={
                            "anatomy": body.anatomy,
                            "male_groin": body.male_groin,
                            "tolerance": body.tolerance,
                        }
                    )
                }
            )
        return updated

    resources = state.resources.model_copy(
        update={
            "owners": tuple(
                owner.model_copy(
                    update={"definitions": tuple(p.definition_id for p in after.purchases)}
                )
                if owner.actor_id == record.actor_id
                else owner
                for owner in state.resources.owners
            ),
            "pools": tuple(rebase(pool) for pool in state.resources.pools),
            "items": tuple(
                item.model_copy(update={"equipped": False, "ready": False})
                if rule.kind in ("alternate-form", "morph")
                and not reverse
                and item.owner_id == record.actor_id
                and item.id not in rule.compatible_equipment_ids
                else item
                for item in state.resources.items
            ),
        }
    )
    if before.statistics is not None and after.statistics is not None:
        resources = refresh_checks(resources, record.actor_id, before, after, command_id)
    state = state.model_copy(
        update={
            "actors": tuple(
                actor.model_copy(
                    update={
                        "proposal": proposal,
                        "approval": approval,
                        "body": record.source_body if reverse else rule.target_body,
                    }
                )
                if actor.actor_id == record.actor_id
                else actor
                for actor in state.actors
            ),
            "resources": resources,
        }
    )
    state = _authority_state(state, rule, reverse=record if reverse else None)
    delta = after.spent - before.spent
    ledger = AdvancementEntry(
        id=command_id + (":revert" if reverse else ":apply"),
        actor_id=record.actor_id,
        kind="transformation",
        points=(
            record.points_charged
            if reverse and rule.point_policy != "adjust"
            else -record.points_charged
            if not reverse and rule.point_policy != "adjust"
            else 0
        ),
        revision=state.revision + 1 if revision is None else revision,
        build_before=before.revision,
        build_after=after.revision,
        reason=("Reverse " if reverse else "Apply ") + rule.kind,
        character_point_delta=delta,
        source_id=rule.id,
    )
    recovery = state.recovery
    if not reverse and rule.kind == "death-transformation":
        pools = tuple(
            pool.model_copy(update={"injury": pool.injury.model_copy(update={"dead": False})})
            if pool.id == f"hp:{record.actor_id}" and pool.injury is not None
            else pool
            for pool in state.resources.pools
        )
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"pools": pools})}
        )
        recovery = recovery.model_copy(
            update={
                "dead_actor_ids": tuple(a for a in recovery.dead_actor_ids if a != record.actor_id)
            }
        )
    memories = tuple(
        m.model_copy(update={"status": "interrupted"})
        if m.actor_id == record.actor_id and m.status == "concentrating"
        else m
        for m in state.transformations.morph_memories
    )
    state = state.model_copy(
        update={
            "advancement": state.advancement + (ledger,),
            "recovery": recovery,
            "transformations": state.transformations.model_copy(
                update={"morph_memories": memories}
            ),
        }
    )

    return reconcile_actor(play.rules_context, state, record.actor_id, command_id)


def _form_distraction_check(
    runtime: RulesContext, state: PlayState, before: PlayState, actor_id: str
) -> CheckTrace | None:
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    old = next((p for p in before.resources.pools if p.id == "hp:" + actor_id), None)
    defended = any(
        e.pending_defense
        and e.pending_defense.defender_id == actor_id
        and any(n.id == e.id and n.pending_defense != e.pending_defense for n in state.encounters)
        for e in before.encounters
    )
    if (
        hp is None
        or old is None
        or (hp.current >= old.current and not defended and not (hp.injury and hp.injury.stunned))
    ):
        return None
    compiled = actor_build(runtime, state, actor_id)
    assert compiled.statistics is not None
    return success_roll(
        compiled.statistics.profile_id,
        compiled.statistics.will - 3,
        check_modifiers(state.resources, actor_id, "will"),
        rng=runtime.rng,
    )


def _shapeshifting_concentration(
    runtime: RulesContext, state: PlayState, before: PlayState
) -> PlayState:
    records = []
    for record in state.transformations.records:
        if (
            record.kind in ("alternate-form", "morph")
            and record.status in ("treatment", "reverting")
            and record.concentration_checked_revision != state.revision
        ):
            if _cannot_concentrate(state, record.actor_id):
                record = record.model_copy(
                    update={"status": "active" if record.status == "reverting" else "interrupted"}
                )
                records.append(record)
                continue
            check = _form_distraction_check(runtime, state, before, record.actor_id)
            if check is not None:
                record = record.model_copy(
                    update={
                        "concentration_checks": record.concentration_checks + (check,),
                        "concentration_checked_revision": state.revision,
                        "status": record.status
                        if check.outcome.succeeded
                        else "active"
                        if record.status == "reverting"
                        else "interrupted",
                    }
                )
        records.append(record)
    memories = []
    for memory in state.transformations.morph_memories:
        if (
            memory.status == "concentrating"
            and memory.concentration_checked_revision != state.revision
        ):
            if _cannot_concentrate(state, memory.actor_id):
                memory = memory.model_copy(update={"status": "interrupted"})
                memories.append(memory)
                continue
            check = _form_distraction_check(runtime, state, before, memory.actor_id)
            if check is not None:
                memory = memory.model_copy(
                    update={
                        "concentration_checks": memory.concentration_checks + (check,),
                        "concentration_checked_revision": state.revision,
                        "status": "concentrating" if check.outcome.succeeded else "interrupted",
                    }
                )
        memories.append(memory)
    return state.model_copy(
        update={
            "transformations": state.transformations.model_copy(
                update={"records": tuple(records), "morph_memories": tuple(memories)}
            )
        }
    )


def shapeshifting_checkpoint(
    play: PlayService, state: PlayState, *, before: PlayState | None = None
) -> PlayState:
    """B83: knockout and death immediately return an active form to its native build."""
    if before is not None:
        state = _shapeshifting_concentration(play.rules_context, state, before)
    for record in state.transformations.records:
        if record.kind not in ("alternate-form", "morph") or record.status not in (
            "active",
            "reverting",
        ):
            continue
        hp = next((p for p in state.resources.pools if p.id == "hp:" + record.actor_id), None)
        fp = next((p for p in state.resources.pools if p.id == "fp:" + record.actor_id), None)
        knocked_out = (
            hp is not None and hp.injury is not None and (hp.injury.dead or hp.injury.unconscious)
        )
        fatigue_out = (
            fp is not None
            and fp.fatigue is not None
            and (fp.fatigue.collapsed or fp.fatigue.unconscious or fp.fatigue.heart_attack)
        )
        if (
            not knocked_out
            and not fatigue_out
            and record.actor_id not in state.recovery.dead_actor_ids
        ):
            continue
        rule = _rule(play, record.actor_id, record.rule_id)
        state = _apply_build(
            play,
            state,
            record,
            rule,
            reverse=True,
            command_id=f"shapeshift-revert:{record.id}:{state.revision}",
            revision=state.revision,
        )
        state = state.model_copy(
            update={
                "transformations": state.transformations.model_copy(
                    update={
                        "records": tuple(
                            r.model_copy(
                                update={
                                    "status": "reverted",
                                    "resolved_at": state.resources.game_time,
                                }
                            )
                            if r.id == record.id
                            else r
                            for r in state.transformations.records
                        )
                    }
                )
            }
        )
    return state


def _reverse_transformation(
    play: PlayService,
    state: PlayState,
    record: TransformationRecord,
    rule: TransformationRule,
    command: ResolveTransformation,
    current: ValidatedBuild,
) -> tuple[PlayState, TransformationRecord]:
    if record.status != "active" and not (
        record.status == "reverting" and command.resolution == "force"
    ):
        raise ValidationError("Only an active transformation can be reversed")
    if command.resolution == "cure" and not rule.curable:
        raise ValidationError("Transformation has no authored cure")
    if command.resolution == "reverse" and not rule.reversible:
        raise ValidationError("Transformation is not reversible")
    if command.resolution == "expire" and (
        record.expires_at is None or state.resources.game_time < record.expires_at
    ):
        raise ValidationError("Transformation has not expired")
    if command.resolution == "expire" and rule.expires_after_seconds is None:
        raise ValidationError("Permanent transformations do not expire")
    if current.revision != record.target_build_revision:
        raise ConflictError("Character changed before transformation reversal")
    if command.resolution == "force" and (
        rule.kind not in ("alternate-form", "morph")
        or command.influence != rule.forced_reversion_influence
    ):
        raise ValidationError("Forced reversion requires the authored external influence")
    state = state.model_copy(
        update={
            "transformations": state.transformations.model_copy(
                update={
                    "morph_memories": tuple(
                        m.model_copy(update={"status": "interrupted"})
                        if m.actor_id == command.actor_id and m.status == "concentrating"
                        else m
                        for m in state.transformations.morph_memories
                    )
                }
            )
        }
    )
    if command.resolution == "reverse" and rule.kind in (
        "alternate-form",
        "morph",
    ):
        record = record.model_copy(
            update={
                "status": "reverting",
                "ready_at": state.resources.game_time + 10,
            }
        )
    else:
        state = _apply_build(play, state, record, rule, reverse=True, command_id=command.id)
        record = record.model_copy(update={"status": "reverted"})
    record = record.model_copy(
        update={
            "status": "reverting" if record.status == "reverting" else "reverted",
            "resolved_at": state.resources.game_time,
        }
    )
    return state, record


def _complete_transformation(
    play: PlayService,
    state: PlayState,
    record: TransformationRecord,
    rule: TransformationRule,
    command: ResolveTransformation,
    current: ValidatedBuild,
    revision: int,
) -> tuple[PlayState, TransformationRecord]:
    if record.status not in ("treatment", "reverting") or state.resources.game_time < (
        record.ready_at or 0
    ):
        raise ValidationError("Transformation treatment is not ready")
    _validate_death_boundary(state, rule)
    if rule.kind in ("alternate-form", "morph"):
        _require_form_concentration(state, command.actor_id)
    if current.revision != (
        record.target_build_revision
        if record.status == "reverting"
        else record.source_build_revision
    ):
        raise ConflictError("Character changed during transformation treatment")
    before = state
    state = settle_actor(play.rules_context, state, record.actor_id, command.id)
    state = state.model_copy(update={"revision": revision})
    state = _shapeshifting_concentration(play.rules_context, state, before)
    checked = next(r for r in state.transformations.records if r.id == record.id)
    if record.status != "reverting" and rule.requires_death != _dead(state, record.actor_id):
        return state, checked.model_copy(
            update={"status": "interrupted", "resolved_at": state.resources.game_time}
        )
    if checked.status != record.status:
        return state, checked.model_copy(update={"resolved_at": state.resources.game_time})
    state = _apply_build(
        play,
        state,
        record,
        rule,
        reverse=record.status == "reverting",
        command_id=command.id,
        revision=revision,
    )
    return state, checked.model_copy(
        update={
            "status": "reverted" if record.status == "reverting" else "active",
            "resolved_at": state.resources.game_time,
            "recovery_until": state.resources.game_time + rule.recovery_seconds
            if rule.recovery_seconds
            else None,
            "expires_at": state.resources.game_time + rule.expires_after_seconds
            if rule.expires_after_seconds
            else None,
        }
    )


def _approve_transformation(
    play: PlayService,
    cid: str,
    state: PlayState,
    record: TransformationRecord,
    rule: TransformationRule,
    command: ApproveTransformation,
    current: ValidatedBuild,
    revision: int,
    principal_id: str,
) -> tuple[PlayState, TransformationRecord]:
    if record.status != "proposed" or current.revision != record.source_build_revision:
        raise ConflictError("Transformation proposal is stale or already decided")
    _validate_death_boundary(state, rule)
    proposal, _, after = _target_build(play, state, rule)
    if after.revision != record.target_build_revision or proposal != record.target_proposal:
        raise ConflictError("Authored transformation target changed")
    charge = max(0, record.point_value_delta) if rule.point_policy in ("charge", "debt") else 0
    if rule.point_policy == "charge" and charge > spendable_points(
        state, command.actor_id, frozenset()
    ):
        raise ValidationError("Transformation overspends earned points")
    state = settle_actor(play.rules_context, state, record.actor_id, command.id)
    if rule.requires_death != _dead(state, record.actor_id) or (
        rule.kind in ("alternate-form", "morph") and _cannot_concentrate(state, record.actor_id)
    ):
        return state, record.model_copy(
            update={"status": "interrupted", "resolved_at": state.resources.game_time}
        )
    approval = play.engine.reviewer.approve(
        record.target_proposal,
        campaign_id=cid,
        actor_id=command.actor_id,
        revision=revision,
        approver_id=principal_id,
        reason=command.reason,
    )
    resources = state.resources
    if rule.payment_item_id is not None:
        resources = play.engine.resources.apply(
            resources,
            Consume(
                id=command.id + ":payment",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                item_id=rule.payment_item_id,
                quantity=rule.payment_quantity,
            ),
        )
    state = state.model_copy(update={"resources": resources})
    status = "treatment" if rule.treatment_seconds else "active"
    record = record.model_copy(
        update={
            "status": status,
            "approved_by": principal_id,
            "approved_at": state.resources.game_time,
            "ready_at": state.resources.game_time + rule.treatment_seconds
            if rule.treatment_seconds
            else None,
            "target_approval": approval,
            "points_charged": charge,
        }
    )
    if status == "active":
        state = _apply_build(
            play,
            state,
            record,
            rule,
            reverse=record.status == "reverting",
            command_id=command.id,
        )
        record = record.model_copy(
            update={
                "resolved_at": state.resources.game_time,
                "recovery_until": state.resources.game_time + rule.recovery_seconds
                if rule.recovery_seconds
                else None,
                "expires_at": state.resources.game_time + rule.expires_after_seconds
                if rule.expires_after_seconds
                else None,
            }
        )
    return state, record


APPROVAL_REFUSAL = "Transformation approval requires director authority"


class TransformationService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        cid: str,
        play: PlayService,
        state: PlayState,
        command: ProposeTransformation | ApproveTransformation | ResolveTransformation,
        value: object,
        *,
        principal_id: str,
    ) -> CommandPlan[TransformationRecord]:
        """What a transformation writes; the pipeline decides whether it runs.

        A director drives approval; anyone else may only act for an actor they
        control. The two are declared rules, not a branch inside the transaction.
        """
        member = member_for(state, principal_id)
        director = Trusted(play.engine.reviewer.gm_ids, refusal=APPROVAL_REFUSAL)
        seated = member.role == "gm" and principal_id in play.engine.reviewer.gm_ids
        payload = json.dumps(
            {"operation": "transformation", "principal": principal_id, "command": value},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            revision = state.revision + 1
            if isinstance(command, ProposeTransformation):
                rule = _rule(play, command.actor_id, command.rule_id)
                before = canonical_build(play, state, command.actor_id)
                if before.revision != command.expected_build_revision:
                    raise ConflictError("Character build changed")
                transformation_rules = play.engine.rules.transformations
                assert transformation_rules is not None
                authored = {
                    authored.id: authored for authored in transformation_rules.transformations
                }
                if any(
                    r.actor_id == command.actor_id
                    and (
                        r.status in ("proposed", "treatment", "reverting")
                        or (
                            r.status == "active"
                            and (
                                authored[r.rule_id].reversible
                                or authored[r.rule_id].curable
                                or authored[r.rule_id].expires_after_seconds is not None
                            )
                        )
                    )
                    for r in state.transformations.records
                ):
                    raise ConflictError("Character already has an unresolved transformation")
                _validate_death_boundary(state, rule)
                proposal, before, after = _target_build(play, state, rule)
                proposal_id = hashlib.sha256(payload.encode()).hexdigest()
                record = TransformationRecord(
                    id=command.id,
                    proposal_id=proposal_id,
                    rule_id=rule.id,
                    actor_id=command.actor_id,
                    kind=rule.kind,
                    status="proposed",
                    source_ref=rule.source_ref,
                    source_proposal=next(
                        a for a in state.actors if a.actor_id == command.actor_id
                    ).proposal,
                    target_proposal=proposal,
                    source_approval=next(
                        a for a in state.actors if a.actor_id == command.actor_id
                    ).approval,
                    source_build_revision=before.revision,
                    target_build_revision=after.revision,
                    source_body_id=current_body_id(state.transformations, command.actor_id),
                    source_body=next(
                        a for a in state.actors if a.actor_id == command.actor_id
                    ).body,
                    target_body_id=rule.target_body_id,
                    trait_routes=rule.trait_routes,
                    attachment_routes=rule.attachment_routes,
                    proposed_by=principal_id,
                    proposed_at=state.resources.game_time,
                    point_value_delta=after.spent - before.spent,
                    source_item_owners=tuple(
                        (item.id, item.owner_id)
                        for item in state.resources.items
                        if item.owner_id == command.actor_id
                    ),
                    source_item_credentials=tuple(
                        (item.id, item.authorized_actor_ids)
                        for item in state.resources.items
                        if command.actor_id in item.authorized_actor_ids
                    ),
                    source_control_principal_ids=tuple(
                        m.principal_id for m in state.members if command.actor_id in m.actor_ids
                    ),
                    source_knowledge_fact_ids=tuple(
                        fact
                        for actor_id, fact in state.world.knowledge
                        if actor_id == command.actor_id
                    ),
                )
                records = state.transformations.records + (record,)
            else:
                record = _active(state, command.proposal_id)
                rule = _rule(play, record.actor_id, record.rule_id)
                if record.actor_id != command.actor_id:
                    raise ValidationError("Transformation proposal belongs to another character")
                current = canonical_build(play, state, command.actor_id)
                if isinstance(command, ApproveTransformation):
                    state, record = _approve_transformation(
                        play, cid, state, record, rule, command, current, revision, principal_id
                    )
                else:
                    if command.resolution == "interrupt":
                        if record.status not in (
                            "treatment",
                            "reverting",
                        ) or state.resources.game_time >= (record.ready_at or 0):
                            raise ValidationError("Only pending treatment may be interrupted")
                        record = record.model_copy(
                            update={
                                "status": "active"
                                if record.status == "reverting"
                                else "interrupted",
                                "resolved_at": state.resources.game_time,
                            }
                        )
                    elif command.resolution == "complete":
                        state, record = _complete_transformation(
                            play, state, record, rule, command, current, revision
                        )
                    else:
                        state, record = _reverse_transformation(
                            play, state, record, rule, command, current
                        )
                records = tuple(
                    record if r.proposal_id == record.proposal_id else r
                    for r in state.transformations.records
                )
            resources = state.resources.model_copy(update={"revision": revision})
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": resources,
                    "approvals": state.approvals
                    + (
                        (record.target_approval,)
                        if record.target_approval is not None
                        and record.target_approval not in state.approvals
                        else ()
                    ),
                    "transformations": state.transformations.model_copy(
                        update={"records": records}
                    ),
                    "rulings": expire_rulings(state.rulings, revision, resources.game_time),
                }
            )
            updated = play.checkpoint(updated, before=play._load(campaign))
            record = next(r for r in updated.transformations.records if r.id == record.id)
            play.commit(campaign, updated)
            return CommandReceipt(action="transformation", outcome=record.model_dump_json())

        async def outcome(campaign: Campaign) -> TransformationRecord:
            state = play._load(campaign)
            result = next(
                (
                    r
                    for r in state.transformations.records
                    if r.id == command.id or r.proposal_id == getattr(command, "proposal_id", "")
                ),
                None,
            )
            if result is None:
                raise ConflictError("Command ID belongs to another operation")
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(
                (Seats(state), director)
                if isinstance(command, ApproveTransformation)
                or (isinstance(command, ResolveTransformation) and command.resolution == "force")
                or seated
                else (Controls(member, command.actor_id),)
            ),
            rng=play.rng,
        )

    async def memorize(self, cid: str, value: object, *, principal_id: str) -> MorphMemory:
        try:
            command = MemorizeMorph.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid Morph memory command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        member = member_for(play._load(await play.store.read(cid)), principal_id)
        payload = json.dumps(
            {
                "operation": "morph-memory",
                "principal": principal_id,
                "command": command.model_dump(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = _memorize_morph(play, play._load(campaign), command)
            revision = state.revision + 1
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            play.commit(campaign, updated)
            memory = next(
                m
                for m in state.transformations.morph_memories
                if m.actor_id == command.actor_id and m.rule_id == command.rule_id
            )
            return CommandReceipt(action="transformation", outcome=memory.model_dump_json())

        async def outcome(campaign: Campaign) -> MorphMemory:
            return next(
                m
                for m in play._load(campaign).transformations.morph_memories
                if m.actor_id == command.actor_id and m.rule_id == command.rule_id
            )

        plan = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Controls(member, command.actor_id),),
            rng=play.rng,
        )
        return await submit(play, cid, plan, principal_id=principal_id)

    async def execute(self, cid: str, value: object, *, principal_id: str) -> TransformationRecord:
        if not isinstance(value, dict):
            raise ValidationError("Invalid transformation command")
        try:
            operation = value.get("operation")
            if operation == "propose":
                command: ProposeTransformation | ApproveTransformation | ResolveTransformation = (
                    ProposeTransformation.model_validate(value)
                )
            elif operation == "approve":
                command = ApproveTransformation.model_validate(value)
            elif operation == "resolve":
                command = ResolveTransformation.model_validate(value)
            else:
                raise ValidationError("Unknown transformation operation")
        except SchemaError as exc:
            raise ValidationError("Invalid transformation command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        return await submit(
            play,
            cid,
            self.plan(cid, play, state, command, value, principal_id=principal_id),
            principal_id=principal_id,
        )
