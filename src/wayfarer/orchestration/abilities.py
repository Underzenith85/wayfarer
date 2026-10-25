"""Authenticated ability commands committed through the existing play store."""

from dataclasses import dataclass, replace

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.supernatural.abilities import fatigue_cost, validate_binding
from wayfarer.engine.rules.supernatural.ability_types import AbilitySpec
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.recovery import interrupt_tasks, require_settled
from wayfarer.engine.simulation.abilities import (
    AbilityContext,
    apply_ability,
    internal_id,
    mind_reading_attacker,
    validate_target,
)
from wayfarer.engine.simulation.ability_offshift import matching
from wayfarer.engine.simulation.ability_offshift import record as record_offshift
from wayfarer.engine.simulation.ability_types import AbilityCommand, AbilityEvent, AbilityOutcome
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import injury_turn
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.equipment.gadget_limitations import (
    GadgetAbilityBinding,
    resolve_gadget_availability,
)
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.enchanting_calendar import require_enchanting_free
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.engine.simulation.traits.malediction_scope import target_cap_scope
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.ability_boundary import AbilityAdmission
from wayfarer.orchestration.ability_offshift_generations import capture as capture_offshift
from wayfarer.orchestration.ability_offshift_generations import payload as offshift_payload
from wayfarer.orchestration.ability_rule16_generations import capture as capture_scope
from wayfarer.orchestration.ability_rule16_generations import payload as scope_payload
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import guard

ABILITY_REFUSAL = "Ability actor is not controlled by principal"


@dataclass(frozen=True)
class AbilityExecutionContext:
    play: PlayService
    rule16_scope: bool = False
    offshift: bool = False


def _require_gadget_available(
    state: PlayState,
    command: AbilityCommand,
    spec: AbilitySpec,
    execution: AbilityExecutionContext,
) -> None:
    if not spec.gadget_modifiers:
        return
    if spec.gadget_actor_id != command.actor_id:
        raise ValidationError("Gadget ability is bound to a different approved actor")
    assert spec.gadget_item_id is not None and spec.gadget_definition_id is not None
    availability = resolve_gadget_availability(
        state.resources,
        GadgetAbilityBinding(
            ability_id=spec.definition_id,
            actor_id=command.actor_id,
            item_id=spec.gadget_item_id,
            definition_id=spec.gadget_definition_id,
        ),
        spec.gadget_modifiers,
        execution.play.engine.resources.specs,
    )
    if not availability.available:
        raise ValidationError(f"Gadget ability unavailable: {availability.reason}")


def _target_scope(
    execution: AbilityExecutionContext,
    state: PlayState,
    spec: AbilitySpec,
    command: AbilityCommand,
    target: ValidatedBuild,
    target_id: str,
) -> bool:
    if (
        not execution.rule16_scope
        or spec.kind not in ("burning-malediction", "mind-reading")
        or command.kind not in ("activate", "resolve")
    ):
        return True
    return target_cap_scope(execution.play.rules_context, state, target, target_id)


def _offshift_admission(
    state: PlayState,
    command: AbilityCommand,
    execution: AbilityExecutionContext,
) -> bool:
    if command.kind == "cancel":
        return False
    captured = matching(state.resources, command)
    continuation = captured is not None and command.kind in ("resolve", "cancel")
    enabled = execution.offshift or continuation
    if not enabled:
        return False
    if state.party.groups or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Off-shift abilities require an individual noncombat clock")
    rules = execution.play.engine.rules.abilities
    spec = (
        next((a for a in rules.abilities if a.definition_id == command.ability_id), None)
        if rules
        else None
    )
    if (
        spec is None
        or spec.kind != "mind-reading"
        or command.kind not in ("activate", "resolve", "cancel")
    ):
        raise ValidationError("Unsupported off-shift ability operation")
    if command.kind == "activate":
        require_enchanting_free(
            state.resources, command.actor_id, through=state.resources.game_time + 1
        )
    return True


def _prepare_ability(
    state: PlayState, command: AbilityCommand, execution: AbilityExecutionContext
) -> tuple[PlayState, AbilitySpec, AbilityContext, Encounter | None]:
    play = execution.play
    guard(state, command.actor_id, "ability")
    offshift = _offshift_admission(state, command, execution)
    if command.kind != "cancel":
        require_innate_actor_action(state, command.actor_id)
        captured = matching(state.resources, command)
        synchronous(
            state,
            command.actor_id,
            enchanting_rest=offshift,
            activity_started_at=captured.effect.started_at
            if captured and command.kind == "resolve"
            else None,
        )
        require_hazards_settled(
            state.resources.hazards, frozenset({command.actor_id}), state.resources.game_time
        )
    rules = play.engine.rules.abilities
    if rules is None:
        raise ValidationError("Campaign has no executable ability bindings")
    spec = next((a for a in rules.abilities if a.definition_id == command.ability_id), None)
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if spec is None or actor is None or actor.approval is None:
        raise ValidationError("Ability unavailable")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
    )
    purchased = next(
        (p for p in actor.proposal.draft.purchases if p.definition_id == spec.definition_id),
        None,
    )
    if purchased is None:
        raise ValidationError("Ability was not purchased and approved")
    # The pinned binding identifies the runtime family; approved purchases
    # choose only combinations that its compiler metadata can execute.
    spec = spec.model_copy(update={"modifiers": (purchased.trait or TraitOptions()).modifiers})
    _require_gadget_available(state, command, spec, execution)
    values = {v.target: int(v.value) for v in build.sheet.values}
    channel = next((c for c in rules.channels if c.id == command.channel_id), None)
    if channel is not None and command.kind != "cancel":
        require_settled(
            state.resources.recovery_tasks,
            frozenset({channel.target_id}),
            state.resources.game_time,
        )
        require_hazards_settled(
            state.resources.hazards, frozenset({channel.target_id}), state.resources.game_time
        )
    target = next((a for a in state.actors if channel and a.actor_id == channel.target_id), None)
    target_values: dict[str, int] = {}
    target_psi_static = False
    target_scope = True
    if target is not None:
        target_build, _ = play.engine.reviewer.activate(
            target.proposal,
            target.approval,
            campaign_id=state.campaign_id,
            actor_id=target.actor_id,
        )
        target_values = {v.target: int(v.value) for v in target_build.sheet.values}
        target_scope = _target_scope(execution, state, spec, command, target_build, target.actor_id)
        target_psi_static = any(
            p.definition_id == "advantage:psi-static" for p in target_build.purchases
        )
    if spec.kind in ("burning-malediction", "mind-reading") and target is None:
        raise ValidationError("Resisted ability requires an authoritative target build")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    incapacitated = bool(actor.conditions or hp.injury and hp.injury.incapacitated)
    if hp.injury and hp.injury.stunned and command.kind in ("activate", "maintain", "analyze"):
        raise ValidationError("Stunned actors must Do Nothing, not concentrate")
    if command.kind != "cancel" and (incapacitated and command.kind != "resolve"):
        raise ValidationError("Incapacitated actor cannot activate an ability")
    if command.kind not in ("cancel", "resolve") and actor.available_at > state.resources.game_time:
        raise ConflictError("Actor is not ready")
    if command.kind in ("activate", "analyze"):
        require_idle_concentration(state.resources, actor.actor_id)
    encounter = next(
        (e for e in state.encounters if e.status == "active" and actor.actor_id in e.turn_order),
        None,
    )
    if encounter is not None:
        if (
            (encounter.pending_defense is not None or encounter.pending_unarmed is not None)
            or encounter.wait_interrupt is not None
            or encounter.current_actor_id != actor.actor_id
        ):
            raise ConflictError("Ability must obey the encounter turn and defense pause")
        participant = next(p for p in encounter.participants if p.actor_id == actor.actor_id)
        if participant.forced_do_nothing and command.kind in ("activate", "analyze"):
            raise ValidationError("Actor must take the required Do Nothing maneuver")
        if channel:
            positions = {p.actor_id: p.position for p in encounter.participants}
            combat = play.engine.combat
            if combat is None or channel.target_id not in positions:
                raise ValidationError("Ability target is outside the encounter")
            channel = channel.model_copy(
                update={
                    "distance_yards": combat.distance(
                        positions[actor.actor_id], positions[channel.target_id]
                    )
                }
            )
    taking_turn = command.kind in ("activate", "analyze")
    context = AbilityContext(
        rules.profile_id,
        purchased.amount,
        purchased.trait or TraitOptions(),
        values["attribute:iq"],
        values["secondary:will"],
        values["secondary:per"],
        values["attribute:ht"],
        target_values.get("secondary:will", 10),
        target_values.get("attribute:ht", 10),
        channel=channel,
        target_psi_static=target_psi_static,
        target_living_or_sapient=target_scope,
        interrupted=incapacitated,
        unavailable=incapacitated and taking_turn,
        shock=hp.injury.shock if hp.injury else 0,
        build_revision=build.revision,
        held_item_ids=tuple(
            i.id
            for i in state.resources.items
            if target is not None
            and i.owner_id == target.actor_id
            and i.ready
            and i.equipped
            and play.engine.resources.specs[i.definition_id].slot in ("hand", "hands")
        ),
    )
    if taking_turn:
        validate_binding(spec, context.level, context.options)
        validate_target(state.world, command, spec, context)
        fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
        if command.kind == "activate" and fatigue_cost(spec) > fp.current and fatigue_cost(spec):
            raise ValidationError("Insufficient fatigue for ability")
        state = injury_turn(
            play.rules_context, state, actor.actor_id, command.id, start=True, do_nothing=False
        )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
        context = replace(context, unavailable=bool(hp.injury and hp.injury.incapacitated))
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"revision": state.revision})}
        )
    return state, spec, context, encounter


def reduce_ability(
    state: PlayState, command: AbilityCommand, execution: AbilityExecutionContext
) -> tuple[PlayState, AbilityOutcome]:
    play = execution.play
    state, spec, context, encounter = _prepare_ability(state, command, execution)
    taking_turn = command.kind in ("activate", "analyze")
    captured = matching(state.resources, command)
    if captured is not None and command.kind == "resolve":
        attacker = mind_reading_attacker(state.resources, command, context, captured.effect)
        if attacker.base_target + sum(m.value for m in attacker.modifiers) < 3:
            raise ValidationError("Voluntary Mind Reading effective skill is below 3")
    resources, world, result = apply_ability(
        state.resources, state.world, command, spec, context, rng=play.rng, system=True
    )
    if execution.offshift and command.kind == "activate":
        resources = record_offshift(resources, command)
    updated = state.model_copy(
        update={
            "revision": resources.revision,
            "resources": resources,
            "world": world,
            "actors": tuple(
                a.model_copy(update={"available_at": resources.game_time + 1})
                if a.actor_id == command.actor_id and result.outcome == "concentrating"
                else a
                for a in state.actors
            ),
        }
    )
    if command.kind in ("activate", "analyze"):
        resources = resources.model_copy(
            update={
                "recovery_tasks": interrupt_tasks(
                    resources.recovery_tasks, frozenset({command.actor_id}), resources.game_time
                )
            }
        )
        updated = updated.model_copy(update={"resources": resources})
    if encounter is not None and command.kind in ("activate", "analyze"):
        combat = play.engine.combat
        assert combat is not None
        participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = combat._advance(
            combat._replace(
                encounter,
                participant.model_copy(
                    update={"last_maneuver": "concentrate", "maneuver_state": ManeuverState()}
                ),
            )
        )
        prior = next(e for e in state.encounters if e.id == encounter.id)
        updated = updated.model_copy(
            update={
                "encounters": tuple(
                    encounter if e.id == encounter.id else e for e in updated.encounters
                )
            }
        )
        if encounter.round > prior.round:
            revision = updated.revision
            updated = play.advance_clock(
                updated.model_copy(update={"revision": state.revision}),
                Advance(
                    id=internal_id(command.id, "round-time"),
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                    to=resources.game_time + encounter.round - prior.round,
                ),
            ).model_copy(update={"revision": revision})
            resources = updated.resources.model_copy(update={"revision": revision})
            updated = updated.model_copy(
                update={
                    "resources": resources,
                    "party": updated.party.model_copy(
                        update={
                            "groups": tuple(
                                g.model_copy(update={"ready_through": resources.game_time})
                                for g in updated.party.groups
                            )
                        }
                    ),
                }
            )
    if taking_turn:
        updated = injury_turn(
            play.rules_context, updated, command.actor_id, command.id, start=False, do_nothing=False
        )
        updated = updated.model_copy(
            update={
                "resources": updated.resources.model_copy(update={"revision": updated.revision})
            }
        )
    if encounter is not None:
        alive = {
            p.id.removeprefix("hp:")
            for p in updated.resources.pools
            if p.id.startswith("hp:")
            and (not p.injury.incapacitated if p.injury else p.current > 0)
        }
        encounter = next(e for e in updated.encounters if e.id == encounter.id)
        ready = {i.id for i in updated.resources.items if i.ready and i.equipped}
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={"ready_item_ids": tuple(i for i in p.ready_item_ids if i in ready)}
                    )
                    for p in encounter.participants
                )
            }
        )
        if len(alive.intersection(encounter.turn_order)) < 2:
            encounter = encounter.model_copy(
                update={"status": "completed", "completion_reason": "incapacitation"}
            )
        else:
            combat = play.engine.combat
            assert combat is not None
            while encounter.current_actor_id not in alive:
                encounter = combat._advance(encounter)
        updated = updated.model_copy(
            update={
                "encounters": tuple(
                    encounter if e.id == encounter.id else e for e in updated.encounters
                )
            }
        )
    return updated, result


class AbilityService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def reduce(
        self,
        state: PlayState,
        command: AbilityCommand,
        *,
        rule16_scope: bool = False,
        offshift: bool = False,
    ) -> PlayState:
        updated, _ = reduce_ability(
            state, command, AbilityExecutionContext(self.play, rule16_scope, offshift)
        )
        return updated

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        member: CampaignMember,
        command: AbilityCommand,
        *,
        principal_id: str,
        rule16_scope: bool = False,
        offshift: bool = False,
    ) -> CommandPlan[AbilityOutcome]:
        """What an ability use writes; the pipeline decides whether it runs."""
        service = AbilityService(play)
        payload = offshift_payload(
            scope_payload(principal_id, command, rule16_scope), principal_id, command, offshift
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            current = play._load(campaign)
            updated = service.reduce(current, command, rule16_scope=rule16_scope, offshift=offshift)
            updated = play.checkpoint(updated, before=current)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="ability")

        async def outcome(campaign: Campaign) -> AbilityOutcome:
            updated = play._load(campaign)
            event = next(e for e in updated.resources.events if e.id == internal_id(command.id))
            return AbilityEvent.model_validate_json(event.kind).outcome

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(
                Seats(state, "player", refusal=ABILITY_REFUSAL),
                Controls(member, command.actor_id, ABILITY_REFUSAL),
            ),
            rng=play.rng,
            ability_admission=AbilityAdmission(command),
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> AbilityOutcome:
        command = AbilityCommand.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = member_for(state, principal_id)
        scope = await capture_scope(play.store, cid, command.id)
        rules = play.engine.rules.abilities
        eligible = (
            command.kind == "activate"
            and rules is not None
            and any(
                a.definition_id == command.ability_id and a.kind == "mind-reading"
                for a in rules.abilities
            )
            and any(
                p.active_work is not None
                and p.status == "active"
                and command.actor_id in p.enchanter_ids
                for p in state.resources.enchantment_projects
            )
        )
        offshift = await capture_offshift(play.store, cid, command.id, eligible=eligible)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                state,
                member,
                command,
                principal_id=principal_id,
                rule16_scope=scope,
                offshift=offshift,
            ),
            principal_id=principal_id,
        )
