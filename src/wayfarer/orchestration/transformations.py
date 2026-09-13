"""Authoritative transformation proposal, approval, treatment, and reversal service."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import ValidatedBuild, pool_limits
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.campaign.transformations import (
    AttachmentKind,
    AttachmentRoute,
    TransformationRecord,
    TransformationRule,
    current_body_id,
)
from wayfarer.engine.simulation.resources import Consume, Pool
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.orchestration.builds import canonical_build, spendable_points
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.fright_builds import refresh_checks
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.play import PlayService


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
    resolution: Literal["complete", "interrupt", "reverse", "cure", "expire"]


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
    if rule.kind == "body-modification":
        changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if any(routes[key] != "body" for key in changed):
            raise ValidationError("Body modification may only alter body-routed traits")
    return proposal, before, after


def _active(state: PlayState, proposal_id: str) -> TransformationRecord:
    record = next(
        (entry for entry in state.transformations.records if entry.proposal_id == proposal_id), None
    )
    if record is None:
        raise ValidationError("Unknown transformation proposal")
    return record


def _validate_death_boundary(state: PlayState, rule: TransformationRule) -> None:
    if rule.requires_death != (rule.actor_id in state.recovery.dead_actor_ids):
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
        owners = dict(reverse.source_item_owners)
        credentials_by_item = dict(reverse.source_item_credentials)
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


def _apply_build(
    play: PlayService,
    state: PlayState,
    record: TransformationRecord,
    rule: TransformationRule,
    *,
    reverse: bool,
    command_id: str,
) -> PlayState:
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
        revision=state.revision + 1,
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
    return state.model_copy(
        update={"advancement": state.advancement + (ledger,), "recovery": recovery}
    )


class TransformationService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

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
        initial = play._load(await play.store.read(cid))
        member = member_for(initial, principal_id)
        gm = member.role == "gm" and principal_id in play.engine.reviewer.gm_ids
        if not gm:
            require_control(member, command.actor_id)
        if isinstance(command, ApproveTransformation) and not gm:
            raise ValidationError("Transformation approval requires director authority")
        payload = json.dumps(
            {"operation": "transformation", "principal": principal_id, "command": value},
            sort_keys=True,
            separators=(",", ":"),
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
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
                        r.status in ("proposed", "treatment")
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
                    if (
                        record.status != "proposed"
                        or current.revision != record.source_build_revision
                    ):
                        raise ConflictError("Transformation proposal is stale or already decided")
                    _validate_death_boundary(state, rule)
                    proposal, _, after = _target_build(play, state, rule)
                    if (
                        after.revision != record.target_build_revision
                        or proposal != record.target_proposal
                    ):
                        raise ConflictError("Authored transformation target changed")
                    charge = (
                        max(0, record.point_value_delta)
                        if rule.point_policy in ("charge", "debt")
                        else 0
                    )
                    if rule.point_policy == "charge" and charge > spendable_points(
                        state, command.actor_id, frozenset()
                    ):
                        raise ValidationError("Transformation overspends earned points")
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
                            play, state, record, rule, reverse=False, command_id=command.id
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
                else:
                    if command.resolution == "interrupt":
                        if record.status != "treatment" or state.resources.game_time >= (
                            record.ready_at or 0
                        ):
                            raise ValidationError("Only pending treatment may be interrupted")
                        record = record.model_copy(
                            update={
                                "status": "interrupted",
                                "resolved_at": state.resources.game_time,
                            }
                        )
                    elif command.resolution == "complete":
                        if record.status != "treatment" or state.resources.game_time < (
                            record.ready_at or 0
                        ):
                            raise ValidationError("Transformation treatment is not ready")
                        _validate_death_boundary(state, rule)
                        if current.revision != record.source_build_revision:
                            raise ConflictError("Character changed during transformation treatment")
                        state = _apply_build(
                            play, state, record, rule, reverse=False, command_id=command.id
                        )
                        record = record.model_copy(
                            update={
                                "status": "active",
                                "resolved_at": state.resources.game_time,
                                "recovery_until": state.resources.game_time + rule.recovery_seconds
                                if rule.recovery_seconds
                                else None,
                                "expires_at": state.resources.game_time + rule.expires_after_seconds
                                if rule.expires_after_seconds
                                else None,
                            }
                        )
                    else:
                        if record.status != "active":
                            raise ValidationError("Only an active transformation can be reversed")
                        if command.resolution == "cure" and not rule.curable:
                            raise ValidationError("Transformation has no authored cure")
                        if command.resolution == "reverse" and not rule.reversible:
                            raise ValidationError("Transformation is not reversible")
                        if command.resolution == "expire" and (
                            record.expires_at is None
                            or state.resources.game_time < record.expires_at
                        ):
                            raise ValidationError("Transformation has not expired")
                        if command.resolution == "expire" and rule.expires_after_seconds is None:
                            raise ValidationError("Permanent transformations do not expire")
                        if current.revision != record.target_build_revision:
                            raise ConflictError("Character changed before transformation reversal")
                        state = _apply_build(
                            play, state, record, rule, reverse=True, command_id=command.id
                        )
                        record = record.model_copy(
                            update={"status": "reverted", "resolved_at": state.resources.game_time}
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
            play.commit(campaign, updated)
            return CommandReceipt(action="transformation", outcome=record.model_dump_json())

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=principal_id,
            rng=play.rng,
        )
        state = play._load(committed["state"])
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
