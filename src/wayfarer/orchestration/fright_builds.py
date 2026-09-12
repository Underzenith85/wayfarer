"""B360-361 lasting consequences through recorded character power approvals.

The GM decides which catalog-backed trait fits the event. The reducer enforces
the exact numeric change; no skill purchases, refunds or unrelated edits ride
along with a fright consequence. Campaign legality and power policy still apply.
"""

import hashlib
import json
from typing import Literal

from pydantic import Field

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import CharacterDraft, ValidatedBuild, pool_limits
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.health.fright import TimedFright, effects, public_id, save
from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class ProposeFrightBuild(Command):
    kind: Literal["propose_fright_build"] = "propose_fright_build"
    fright_id: str
    expected_build_revision: str
    draft: CharacterDraft
    related_trait_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)


class ApproveFrightBuild(Command):
    kind: Literal["approve_fright_build"] = "approve_fright_build"
    fright_id: str
    proposal_id: str
    reason: str = Field(min_length=1, max_length=2000)


def validate_change(
    play: PlayService,
    state: PlayState,
    item: TimedFright,
    draft: CharacterDraft,
    related_trait_id: str | None,
) -> ValidatedBuild:
    actor = next(a for a in state.actors if a.actor_id == item.actor_id)
    old_draft = actor.proposal.draft
    old_build = build(play.rules_context, state, item.actor_id)
    effect = item.effect
    if item.adjudicated_build_revision is not None:
        raise ConflictError("Fright build consequence is already resolved")
    if not (effect.permanent_ht_loss or effect.permanent_iq_loss or effect.trait_choice != "none"):
        raise ValidationError("This fright result has no lasting build change")
    if (draft.name, draft.backstory) != (old_draft.name, old_draft.backstory):
        raise ValidationError("A fright consequence cannot rewrite character identity")
    before = {p.definition_id: p for p in old_draft.purchases}
    after = {p.definition_id: p for p in draft.purchases}
    if len(after) != len(draft.purchases) or not before.keys() <= after.keys():
        raise ValidationError("Fright changes cannot remove or duplicate purchases")
    changed = {key for key in after if before.get(key) != after[key]}
    for key, loss in (
        ("attribute:ht", effect.permanent_ht_loss),
        ("attribute:iq", effect.permanent_iq_loss),
    ):
        if loss:
            original = before.get(key)
            if original is None or after.get(key) != original.model_copy(
                update={"amount": original.amount - loss}
            ):
                raise ValidationError("Fright requires its exact permanent attribute loss")
            changed.remove(key)
    compiler = play.engine.reviewer.compiler
    for key in changed:
        definition = compiler.definitions.get(key)
        if definition is None or definition.kind is not DefinitionKind.TRAIT:
            raise ValidationError("Only consequence traits may accompany the attribute loss")
    proposal = actor.proposal.model_copy(update={"draft": draft})
    review = play.engine.reviewer.review(proposal)
    result = review.compilation.build
    if result is None:
        raise ValidationError("Consequence build is not legal under the pinned campaign policy")
    costs_before = {p.definition_id: p.cost for p in old_build.purchases}
    costs_after = {p.definition_id: p.cost for p in result.purchases}
    choice = effect.trait_choice
    if choice == "none":
        if changed or related_trait_id:
            raise ValidationError("This consequence permits no trait edits")
        return result
    if choice == "worsen-self-control" and related_trait_id is not None:
        original = before.get(related_trait_id)
        definition = compiler.definitions.get(related_trait_id)
        if (
            original is None
            or original.trait is None
            or original.trait.self_control is None
            or definition is None
            or definition.trait_rules is None
            or not definition.trait_rules.self_control
            or costs_before.get(related_trait_id, 0) >= 0
        ):
            raise ValidationError("Related trait must be an owned self-control disadvantage")
        rating = original.trait.self_control
        if rating > 6:
            expected = original.model_copy(
                update={"trait": original.trait.model_copy(update={"self_control": rating - 3})}
            )
            if changed != {related_trait_id} or after[related_trait_id] != expected:
                raise ValidationError("Self-control must worsen by exactly one step")
            return result
    elif related_trait_id is not None:
        raise ValidationError("Related trait applies only to self-control worsening")
    if len(changed) != 1 or any(key in before for key in changed):
        raise ValidationError("This fright result requires one new disadvantage")
    selected = next(iter(changed))
    if costs_after[selected] >= 0 or costs_after[selected] != effect.trait_points:
        raise ValidationError("Selected disadvantage does not match the fright point value")
    # The GM's approval attests to the mental/physical/delusion classification
    # and its connection to the event; executable definitions still own costs.
    return result


class FrightBuildService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, principal_id: str) -> None:
        if not isinstance(value, dict):
            raise ValidationError("Invalid fright build command")
        model = (
            ProposeFrightBuild
            if value.get("kind") == "propose_fright_build"
            else ApproveFrightBuild
        )
        try:
            command = model.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid fright build command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        access = CampaignAccess(play)
        member = access._member(play._load(await play.store.read(cid)), principal_id)
        if member.role != "gm":
            access._control(member, command.actor_id)
        elif principal_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Fright build proposals require a configured director")
        if isinstance(command, ApproveFrightBuild) and (
            member.role != "gm" or principal_id not in play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Fright build approval requires director authority")
        payload = json.dumps(
            {
                "operation": "fright-build",
                "principal": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            item = next(
                (
                    i
                    for i in effects(state.resources)
                    if i.actor_id == command.actor_id and command.fright_id in (i.id, public_id(i))
                ),
                None,
            )
            if item is None:
                raise ValidationError("Unknown fright consequence")
            current = build(play.rules_context, state, command.actor_id)
            revision = state.revision + 1
            if isinstance(command, ProposeFrightBuild):
                if command.expected_build_revision != current.revision:
                    raise ConflictError("Character build changed")
                validate_change(play, state, item, command.draft, command.related_trait_id)
                proposal_id = hashlib.sha256(payload.encode()).hexdigest()
                item = item.model_copy(
                    update={
                        "proposed_draft": command.draft.model_dump_json(),
                        "proposal_id": proposal_id,
                        "proposal_build_revision": current.revision,
                        "proposed_by": principal_id,
                        "proposed_related_trait": command.related_trait_id,
                    }
                )
            else:
                if (
                    item.proposal_id != command.proposal_id
                    or item.proposed_draft is None
                    or item.proposal_build_revision != current.revision
                ):
                    raise ConflictError("Fright build proposal is stale or has changed")
                draft = CharacterDraft.model_validate_json(item.proposed_draft)
                compiled = validate_change(play, state, item, draft, item.proposed_related_trait)
                actor = next(a for a in state.actors if a.actor_id == command.actor_id)
                proposal = actor.proposal.model_copy(update={"draft": draft})
                approval = play.engine.reviewer.approve(
                    proposal,
                    campaign_id=cid,
                    actor_id=command.actor_id,
                    revision=revision,
                    approver_id=principal_id,
                    reason=command.reason,
                )
                maxima = pool_limits(compiled)
                resources = state.resources.model_copy(
                    update={
                        "owners": tuple(
                            o.model_copy(
                                update={
                                    "definitions": tuple(
                                        p.definition_id for p in compiled.purchases
                                    )
                                }
                            )
                            if o.actor_id == command.actor_id
                            else o
                            for o in state.resources.owners
                        ),
                        "pools": tuple(
                            _refreshed(
                                p,
                                maxima[p.id.split(":", 1)[0]],
                                compiled,
                                physical_traits(
                                    compiled, play.engine.reviewer.compiler.definitions
                                ),
                            )
                            if p.id in ("hp:" + command.actor_id, "fp:" + command.actor_id)
                            else p
                            for p in state.resources.pools
                        ),
                    }
                )
                resources = refresh_checks(
                    resources, command.actor_id, current, compiled, command.id
                )
                item = next(i for i in effects(resources) if i.id == item.id)
                state = state.model_copy(
                    update={
                        "actors": tuple(
                            a.model_copy(update={"proposal": proposal, "approval": approval})
                            if a.actor_id == command.actor_id
                            else a
                            for a in state.actors
                        ),
                        "approvals": state.approvals + (approval,),
                        "resources": resources,
                    }
                )
                item = item.model_copy(
                    update={
                        "adjudicated_build_revision": compiled.revision,
                        "proposed_draft": None,
                        "proposal_id": None,
                    }
                )
            resources = save(state.resources, item, command.id).model_copy(
                update={"revision": revision}
            )
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "resources": resources,
                    "rulings": expire_rulings(state.rulings, revision, resources.game_time),
                }
            )
            play.commit(campaign, updated)
            return CommandReceipt(action="npc", outcome="fright build decision recorded")

        await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=principal_id,
            rng=play.rng,
        )


def refresh_checks(
    resources: ResourceState,
    actor_id: str,
    before: ValidatedBuild,
    after: ValidatedBuild,
    command_id: str,
) -> ResourceState:
    """Rebase stored recovery inputs without changing their original deadlines.

    Medical skill snapshots are IQ-based; Swimming uses HT. Existing spell and
    ability build pins continue to require cancellation if their build changed.
    """
    old, new = before.statistics, after.statistics
    assert old is not None and new is not None
    ht, iq, will = new.ht - old.ht, new.iq - old.iq, new.will - old.will
    for item in effects(resources):
        if item.actor_id != actor_id or not item.active:
            continue
        delta = (
            ht
            if item.effect.recovery_attribute == "ht"
            else will
            if item.effect.recovery_attribute in ("will", "modified-will")
            else 0
        )
        if delta:
            resources = save(
                resources,
                item.model_copy(update={"recovery_target": item.recovery_target + delta}),
                command_id + ":rebase:" + item.id,
            )
    tasks = tuple(
        t.model_copy(
            update={
                "ht": t.ht + (ht if t.target_id == actor_id else 0),
                "skill": t.skill + (iq if t.actor_id == actor_id else 0)
                if t.skill is not None
                else None,
                "physician_skill": t.physician_skill + (iq if t.physician_id == actor_id else 0)
                if t.physician_skill is not None
                else None,
            }
        )
        if not t.settled
        else t
        for t in resources.recovery_tasks
    )
    hazards = tuple(
        h.model_copy(update={"ht": h.ht + ht, "will": h.will + will, "swimming": h.swimming + ht})
        if h.actor_id == actor_id and h.active
        else h
        for h in resources.hazards
    )
    return resources.model_copy(update={"recovery_tasks": tasks, "hazards": hazards})
