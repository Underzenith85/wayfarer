"""Approved spell transactions on the existing campaign ledger; no frozen v1 route."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.casting_targeting import awaken_checks
from wayfarer.engine.simulation.magic.item_receipt_privacy import item_result
from wayfarer.engine.simulation.magic.spell_transitions import RuntimeSpellResolver
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellExecutionContext as SpellExecutionContext,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellResolver as SpellResolver,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    _recorded_spell_result as _recorded_spell_result,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    advance_cast_turn as advance_cast_turn,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    apparent_result as apparent_result,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    approved_context as approved_context,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    combat_guard as combat_guard,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    reduce_spell as reduce_spell,
)
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    RuntimeSpellCommand,
    SpellCommand,
    SpellEvent,
    SpellResult,
    event_id,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Control, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_generations import recorded_generations


def _runtime_resolver(resolve: SpellResolver | None) -> RuntimeSpellResolver | None:
    if resolve is None:
        return None

    def legacy(
        runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand
    ) -> SpellEnvironment:
        return resolve(runtime, state, SpellCommand.model_validate(command.model_dump()))

    return legacy


async def _player_result(
    play: PlayService, saved: PlayState, command: SpellCommand, result: SpellResult
) -> SpellResult:
    rules = play.engine.rules.spells
    channel = (
        next((c for c in rules.channels if c.id == command.channel_id), None) if rules else None
    )
    private_checks = False
    if result.checks and channel is not None and channel.magic_item_id is not None:
        original = play._load(await play.store.replay(saved.campaign_id, command.expected_revision))
        presented = item_result(
            original.resources,
            result,
            item_id=channel.magic_item_id,
            spell_id=command.spell_id,
        )
        private_checks = presented is not result
        result = presented
    result = apparent_result(saved.resources, command, result)
    if private_checks:
        return result
    subjects = awaken_checks(saved.resources, command.cast_id)
    if not subjects:
        return result
    if len(subjects) > len(result.checks):
        raise ValidationError("Recorded Awaken subject checks are inconsistent")
    current = play._load(await play.store.read(saved.campaign_id))
    known = {e.id for e in current.world.perspective(command.actor_id).entities} | {
        command.actor_id
    }
    prefix = len(result.checks) - len(subjects)
    return result.model_copy(
        update={
            "checks": result.checks[:prefix]
            + tuple(
                trace
                for actor_id, trace in zip(subjects, result.checks[prefix:], strict=True)
                if actor_id in known
            )
        }
    )


class SpellService:
    """An internal transaction seam, not permission to invent spell bindings."""

    def __init__(self, play: PlayService, resolve: SpellResolver | None = None) -> None:
        self.play, self.resolve = play, resolve

    def plan(
        self,
        play: PlayService,
        member: CampaignMember,
        command: SpellCommand,
        *,
        principal_id: str,
        capture_targeting: bool = True,
        check_symptoms: bool = True,
        item_sight: bool = True,
        area_targeting: bool = True,
        missile_attack: bool = True,
        state: PlayState | None = None,
    ) -> CommandPlan[SpellResult]:
        """What a spell lifecycle command writes; the pipeline decides whether it runs.

        Two principals reach this family. A seated player casts for an actor they
        control and reads back only what the table can see; the director drives the
        lifecycle and reads the recorded result. Membership says which, so the two
        differ in their declared rules and in what they read back, not in a branch
        inside the transaction.
        """
        player = member.role == "player"
        if player and self.resolve is not None:
            raise AuthorizationError("Spell actor is not controlled by principal")
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Spell lifecycle requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-lifecycle",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
                **({"targeting_generation": 1} if capture_targeting else {}),
                **({"check_generation": 1} if check_symptoms else {}),
                **({"item_sight_generation": 1} if item_sight else {}),
                **({"area_targeting_generation": 1} if area_targeting else {}),
                **({"missile_attack_generation": 1} if missile_attack else {}),
            },
            sort_keys=True,
        )

        execution = SpellExecutionContext(
            play.rules_context,
            _runtime_resolver(self.resolve),
            capture_targeting=capture_targeting,
            check_symptoms=check_symptoms,
            item_sight=item_sight,
            area_targeting=area_targeting,
        )
        control: tuple[Control, ...] = (
            Controls(member, command.actor_id, "Spell actor is not controlled by principal")
            if player
            else Trusted(
                play.engine.reviewer.gm_ids,
                refusal="Spell lifecycle requires trusted director authority",
            ),
        )
        if not player and (
            item_sight and command.kind == "cancel" or command.spell_id in ("create-fire", "awaken")
        ):
            if state is None:
                raise ValidationError("Spell lifecycle requires current campaign membership")
            control = (Seats(state), *control)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            with combat_generation(
                frozenset({"missile-interposition"}) if missile_attack else frozenset()
            ):
                updated, result = reduce_spell(before, command, execution)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            result = _recorded_spell_result(updated, command)
            # Roll targets, opposed traces, and bindings stay in the private ledger.
            return CommandReceipt(
                action="resource",
                outcome="spell:" + apparent_result(updated.resources, command, result).outcome,
            )

        async def outcome(campaign: Campaign) -> SpellResult:
            saved = play._load(campaign)
            recorded = next(e for e in saved.resources.events if e.id == event_id(command.id))
            result = SpellEvent.model_validate_json(recorded.kind).result
            if not player:
                return result
            return await _player_result(play, saved, command, result)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=control,
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> SpellResult:
        command = SpellCommand.model_validate(value)
        if command.target_item_id is not None and command.kind != "release":
            raise ValidationError("Object targeting requires a missile release")
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = member_for(state, principal_id)
        generations = await recorded_generations(play, state, command)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                member,
                command,
                principal_id=principal_id,
                capture_targeting=generations.capture_targeting,
                check_symptoms=generations.check_symptoms,
                item_sight=generations.item_sight,
                area_targeting=generations.area_targeting,
                missile_attack=generations.missile_attack,
                state=state,
            ),
            principal_id=principal_id,
        )
