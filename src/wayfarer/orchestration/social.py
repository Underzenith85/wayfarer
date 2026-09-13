"""Internal, transactional social dispatch; no player-supplied rules context."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.traits.social import (
    bind_standing,
    reaction_modifiers,
    skill_conditions,
)
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.rules.skills.mundane.social.inventory import Resolution, require_procedure
from wayfarer.engine.rules.traits.mundane.runtime import Check
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.development import bind_teaching_outcome
from wayfarer.engine.simulation.campaign.economics import bind_social_material_outcome
from wayfarer.engine.simulation.campaign.npcs import NPCSocialRules
from wayfarer.engine.simulation.campaign.party import bind_leadership_outcome
from wayfarer.engine.simulation.campaign.propaganda import bind_media
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.health.fright import apply_effect, validate_subject
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    SocialOutcome,
    apply_interaction,
    apply_social,
)
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.play import PlayService


@dataclass(frozen=True)
class ResolvedInteraction:
    context: SocialContext
    disclosure: SocialDisclosure = SocialDisclosure()


InteractionResolver = Callable[[PlayService, PlayState, SocialCommand], ResolvedInteraction]


def _collapse(encounters: tuple[Encounter, ...], actor_id: str) -> tuple[Encounter, ...]:
    """Put an active combat participant prone without rewriting completed fights."""
    return tuple(
        encounter.model_copy(
            update={
                "participants": tuple(
                    participant.model_copy(update={"posture": "prone"})
                    if participant.actor_id == actor_id
                    else participant
                    for participant in encounter.participants
                )
            }
        )
        if encounter.status == "active"
        and any(participant.actor_id == actor_id for participant in encounter.participants)
        else encounter
        for encounter in encounters
    )


def bind_trait_modifiers(
    play: PlayService, state: PlayState, command: SocialCommand, context: SocialContext
) -> None:
    """Add the reaction/influence modifiers the initiator's approved build implies.

    An approved build is the only source: a resolver cannot supply a trait
    modifier, and an unbound or unpurchased trait contributes nothing. An
    unapproved initiator (an NPC without a build) contributes nothing either.
    """

    check: Check = "influence" if command.kind in ("influence", "skill") else "reaction"
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        context.bind_trait_modifiers(())
        return

    approved = build(play.rules_context, state, command.actor_id)
    definitions = play.engine.reviewer.compiler.definitions
    context.standing = bind_standing(approved, definitions, context.standing, context.modifiers)
    context.bind_trait_modifiers(
        reaction_modifiers(
            approved,
            definitions,
            check,
            context.audience,
        )
    )


def bind_skill_conditions(
    play: PlayService, state: PlayState, command: SocialCommand, context: SocialContext
) -> None:
    """Derive the #345 procedure's build-supplied conditions and reaction modifiers.

    The approved build decides only whether a named condition holds; the procedure
    owns what each one is worth. An initiator without an approved build asserts
    nothing extra, and a resolver still cannot supply a trait modifier itself.
    """

    if context.procedure_id is None:
        raise ValidationError("Social skill dispatch requires a declared procedure")
    procedure = require_procedure(context.profile_id, context.procedure_id)
    if procedure.id != "skill:propaganda" and context.medium_id is not None:
        raise ValidationError("Only Propaganda can select an authored medium")
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        context.bind_trait_modifiers(())
        return

    approved = build(play.rules_context, state, command.actor_id)
    if procedure.id == "skill:propaganda":
        npc_rules = play.engine.rules.npcs
        context.media = bind_media(
            npc_rules.propaganda if isinstance(npc_rules, NPCSocialRules) else None,
            profile_id=context.profile_id,
            campaign_technology_level=play.engine.reviewer.compiler.policy.technology_level,
            medium_id=context.medium_id,
            actor_id=command.actor_id,
            build=approved,
            resources=state.resources,
        )
    definitions = play.engine.reviewer.compiler.definitions
    context.conditions = context.conditions | skill_conditions(
        approved, definitions, procedure.id, context.audience
    )
    if procedure.resolution is not Resolution.INFLUENCE:
        # Reaction modifiers reach influence rolls only (B359); an unopposed
        # procedure must not silently collect them.
        context.bind_trait_modifiers(())
        return
    bind_trait_modifiers(play, state, command, context)


def dispatch(
    play: PlayService,
    before: PlayState,
    command: SocialCommand,
    interaction: ResolvedInteraction,
) -> tuple[PlayState, SocialOutcome]:
    """Trusted reducer shared by director commands and authored NPC occurrences."""
    profile_id = play.engine.reviewer.compiler.statistics_profile
    if interaction.context.profile_id != profile_id:
        raise ValidationError("Social context does not match campaign profile")
    if command.kind == "fright":
        validate_subject(before.resources, command.subject_id, profile_id)

        if not any(a.actor_id == command.subject_id for a in before.actors):
            raise ValidationError("Fright requires an approved character")
        statistics = build(play.rules_context, before, command.subject_id).statistics
        assert statistics is not None
        if interaction.context.ht != statistics.ht or interaction.context.will != statistics.will:
            raise ValidationError("Fright context must match approved HT and Will")
    # A player subject may resist fear or a disadvantage, but reaction, influence
    # and skill procedures never select behavior or disclose facts on their behalf.
    if command.kind in ("reaction", "influence", "skill"):
        if any(m.role == "player" and command.subject_id in m.actor_ids for m in before.members):
            raise ValidationError("NPC social outcomes cannot control a player character")
        if command.kind == "skill":
            bind_skill_conditions(play, before, command, interaction.context)
        else:
            bind_trait_modifiers(play, before, command, interaction.context)
    replay = any(receipt.command_id == command.id for receipt in before.resources.receipts)
    resources, world, outcome = apply_interaction(
        before.resources,
        before.world,
        command,
        interaction.context,
        interaction.disclosure,
        rng=play.rng,
        system=True,
    )
    if outcome.media is not None and not replay:
        resources = play.engine.resources.apply(
            resources,
            Advance(
                id=f"{command.id}:propaganda-time",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                to=resources.game_time + outcome.media.attempt_seconds,
            ),
            system=True,
            rng=play.rng,
        )
    encounters = before.encounters
    development = before.development
    economics = before.economics
    party = before.party
    if command.kind == "skill" and interaction.context.procedure_id == "skill:teaching":
        development = bind_teaching_outcome(
            development,
            play.engine.rules.development,
            command_id=command.id,
            trigger_id=command.trigger_id,
            teacher_id=command.actor_id,
            student_id=command.subject_id,
            outcome=outcome.outcome,
        )
    if command.kind == "skill" and interaction.context.procedure_id == "skill:leadership":
        party = bind_leadership_outcome(
            play.engine.rules.party,
            party,
            command_id=command.id,
            trigger_id=command.trigger_id,
            leader_actor_id=command.actor_id,
            subject_id=command.subject_id,
            outcome=outcome.outcome,
            player_actor_ids=frozenset(
                actor_id
                for member in before.members
                if member.role == "player"
                for actor_id in member.actor_ids
            ),
        )
    material_procedures = {
        "skill:carousing",
        "skill:panhandling",
        "skill:performance",
        "skill:public-speaking",
    }
    if command.kind == "skill" and interaction.context.procedure_id in material_procedures:
        private = json.loads(resources.events[-1].kind)["private"]
        check = private.get("check")
        if not isinstance(check, dict) or type(check.get("margin")) is not int:
            raise ValidationError("Social material outcome requires its recorded success margin")
        actor = next((value for value in before.actors if value.actor_id == command.actor_id), None)
        approved = (
            build(play.rules_context, before, command.actor_id)
            if actor is not None and actor.approval is not None
            else None
        )
        economics, resources = bind_social_material_outcome(
            economics,
            resources,
            play.engine.rules.economics,
            command_id=command.id,
            trigger_id=command.trigger_id,
            procedure_id=interaction.context.procedure_id,
            actor_id=command.actor_id,
            margin=check["margin"],
            outcome=outcome.outcome,
            critical_success=check.get("outcome") == "critical-success",
            ht=interaction.context.ht,
            purchased_ids=frozenset(value.definition_id for value in approved.purchases)
            if approved is not None
            else frozenset(),
            actor_ids=frozenset(
                value.id for value in before.world.entities if value.kind is EntityKind.ACTOR
            ),
            rng=play.rng,
        )
    if command.kind == "fright":
        raw = json.loads(resources.events[-1].kind)["private"]["effect"]
        if raw is not None:
            effect = FrightEffect.model_validate_json(json.dumps(raw))
            resources = apply_effect(
                resources,
                effect,
                actor_id=command.subject_id,
                trigger_id=command.trigger_id,
                command_id=command.id,
                ht=interaction.context.ht,
                will=interaction.context.will,
                modified_will=interaction.context.target,
                rng=play.rng,
            )
            resources = resources.model_copy(update={"revision": before.revision + 1})
            if effect.collapse:
                # Apply the table's physical fall through the authoritative
                # encounter aggregate.  Injury knockdown alone is insufficient:
                # a faint or seizure falls even when its HP loss is zero.
                encounters = _collapse(before.encounters, command.subject_id)
    updated = before.model_copy(
        update={
            "revision": resources.revision,
            "resources": resources,
            "world": world,
            "encounters": encounters,
            "development": development,
            "economics": economics,
            "party": party,
        }
    )
    return updated, outcome


class SocialService:
    """Bind a trusted scenario/NPC trigger resolver, then commit through PlayService.

    This adapter does not enable an unverified profile in the profile registry.
    The resolver must reject unrecognized triggers and derive effective values
    from approved character builds and bounded scenario definitions.
    """

    def __init__(self, play: PlayService, resolve: InteractionResolver) -> None:
        self.play, self.resolve = play, resolve

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> SocialOutcome:
        try:
            command = SocialCommand.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid social command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = member_for(state, authenticated_gm_id)
        if member.role != "gm" or authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Social dispatch requires trusted director authority")
        profile_id = play.engine.reviewer.compiler.statistics_profile
        if profile_id is None:
            raise ValidationError("Social dispatch requires an exact GURPS profile")
        payload = json.dumps(
            {
                "operation": "gurps-social",
                "principal_id": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, outcome = dispatch(play, before, command, self.resolve(play, before, command))
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            # The event stream carries neither trusted modifiers nor fact IDs.
            return CommandReceipt(action="npc", outcome=outcome.model_dump_json())

        result = await commit_command(
            play,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
            rng=play.rng,
        )
        committed = play._load(result["state"])
        # Receipt replay must not invoke the resolver again or re-evaluate facts.
        _, outcome = apply_social(
            committed.resources,
            committed.world,
            command,
            SocialContext(profile_id, 0),
            rng=play.rng,
            system=True,
        )
        return outcome
