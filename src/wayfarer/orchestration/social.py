"""Internal, transactional social dispatch; no player-supplied rules context."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.development import bind_teaching_outcome
from wayfarer.engine.simulation.campaign.economics import bind_social_material_outcome
from wayfarer.engine.simulation.campaign.party import bind_leadership_outcome
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
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import AuthoredSocialReaction, PrepareReaction
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.social_binding import bind_skill_conditions, bind_trait_modifiers
from wayfarer.orchestration.social_generations import correct_social_reactions
from wayfarer.orchestration.social_replay import (
    CapturedSocialContext,
    CapturedSocialSource,
    captured_source,
)
from wayfarer.orchestration.task_records import TaskResult
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.events import CommandInput


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


def _require_voluntary_social(state: PlayState, command: SocialCommand) -> None:
    if command.kind in ("influence", "skill"):
        require_innate_actor_action(state, command.actor_id)


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
        statistics = build(
            play.rules_context, before, command.subject_id, defensive=True
        ).statistics
        assert statistics is not None
        if interaction.context.ht != statistics.ht or interaction.context.will != statistics.will:
            raise ValidationError("Fright context must match approved HT and Will")
    # A player subject may resist fear or a disadvantage, but reaction, influence
    # and skill procedures never select behavior or disclose facts on their behalf.
    _require_voluntary_social(before, command)
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
        correct_reactions=correct_social_reactions(),
    )
    if outcome.media is not None and not replay:
        before = play.advance_clock(
            before.model_copy(update={"world": world, "resources": resources}),
            Advance(
                id=f"{command.id}:propaganda-time",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                to=resources.game_time + outcome.media.attempt_seconds,
            ),
        )
        resources, world = before.resources, before.world
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


DIRECTOR_REFUSAL = "Social dispatch requires trusted director authority"


class SocialService:
    """Bind a trusted scenario/NPC trigger resolver, then commit through PlayService.

    This adapter does not enable an unverified profile in the profile registry.
    The resolver must reject unrecognized triggers and derive effective values
    from approved character builds and bounded scenario definitions.
    """

    def __init__(self, play: PlayService, resolve: InteractionResolver) -> None:
        self.play, self.resolve = play, resolve

    async def prepare_reaction(
        self,
        cid: str,
        command: SocialCommand,
        source: AuthoredSocialReaction,
        *,
        principal_id: str,
    ) -> TaskResult:
        """Record a reconstructible GM source before this reaction's secret dice.

        The ordinary resolver remains the legacy immediate-roll route. A pending
        source is explicit so seed reexecution never invokes an unrecorded callback.
        """
        if (command.kind, command.trigger_id, command.subject_id) != (
            source.mode,
            source.trigger_id,
            source.subject_id,
        ):
            raise ValidationError("Prepared reaction source does not match its social command")
        return await TaskService(self.play).execute(
            cid,
            PrepareReaction(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                source=source,
            ),
            principal_id=principal_id,
        )

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: SocialCommand,
        *,
        principal_id: str,
        source: CapturedSocialSource | None = None,
    ) -> CommandPlan[SocialOutcome]:
        """What a social dispatch writes; the pipeline decides whether it runs."""
        profile_id = play.engine.reviewer.compiler.statistics_profile
        if profile_id is None:
            raise ValidationError("Social dispatch requires an exact GURPS profile")
        payload = json.dumps(
            {
                "operation": "gurps-social",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
                **({"social_source": source.model_dump(mode="json")} if source is not None else {}),
            },
            sort_keys=True,
        )

        def require_current_trust(principal: str) -> None:
            Trusted(play.engine.reviewer.gm_ids, refusal=DIRECTOR_REFUSAL)(principal)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            interaction = (
                ResolvedInteraction(source.context.resolve(), source.disclosure)
                if source is not None
                else self.resolve(play, before, command)
            )
            updated, outcome = dispatch(play, before, command, interaction)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            # The event stream carries neither trusted modifiers nor fact IDs.
            return CommandReceipt(action="npc", outcome=outcome.model_dump_json())

        async def outcome(campaign: Campaign) -> SocialOutcome:
            committed = play._load(campaign)
            # Receipt replay must not invoke the resolver again or re-evaluate facts.
            _, result = apply_social(
                committed.resources,
                committed.world,
                command,
                SocialContext(profile_id, 0),
                rng=play.rng,
                system=True,
            )
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(
                Seats(state, refusal=DIRECTOR_REFUSAL),
                require_current_trust,
            ),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> SocialOutcome:
        try:
            command = SocialCommand.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid social command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        # No resolver runs for an unauthorized caller or a committed retry. The
        # pipeline repeats current authority under the transaction lock.
        Seats(state, refusal=DIRECTOR_REFUSAL)(principal_id)
        Trusted(play.engine.reviewer.gm_ids, refusal=DIRECTOR_REFUSAL)(principal_id)
        prior = recorded_command.get()
        saved = (
            CommandInput(prior.payload_hash, prior.command_input)
            if prior is not None
            else await play.store.command_input(cid, command.id)
        )
        source = captured_source(saved) if saved is not None else None
        if saved is None:
            interaction = self.resolve(play, state, command)
            source = CapturedSocialSource(
                context=CapturedSocialContext.model_validate(vars(interaction.context)),
                disclosure=interaction.disclosure,
            )
        return await submit(
            play,
            cid,
            self.plan(play, state, command, principal_id=principal_id, source=source),
            principal_id=principal_id,
        )
