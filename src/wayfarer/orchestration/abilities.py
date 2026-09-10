"""Authenticated ability commands committed through the existing play store."""

from dataclasses import replace

from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.gurps_melee import injury_turn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import guard
from wayfarer.rules.abilities import fatigue_cost, validate_binding
from wayfarer.rules.hazard_types import require_hazards_settled
from wayfarer.rules.recovery_types import interrupt_tasks
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.abilities import (
    AbilityContext,
    apply_ability,
    internal_id,
    validate_target,
)
from wayfarer.simulation.ability_types import AbilityCommand, AbilityEvent, AbilityOutcome
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.concentration import require_idle_concentration
from wayfarer.simulation.maneuvers import ManeuverState
from wayfarer.simulation.party import synchronous
from wayfarer.simulation.resources import Advance


class AbilityService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def reduce(self, state: PlayState, command: AbilityCommand) -> PlayState:
        guard(state, command.actor_id, "ability")
        if command.kind != "cancel":
            synchronous(state, command.actor_id)
            require_hazards_settled(
                state.resources.hazards, frozenset({command.actor_id}), state.resources.game_time
            )
        rules = self.play.engine.rules.abilities
        if rules is None:
            raise ValidationError("Campaign has no executable ability bindings")
        spec = next((a for a in rules.abilities if a.definition_id == command.ability_id), None)
        actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
        if spec is None or actor is None or actor.approval is None:
            raise ValidationError("Ability unavailable")
        build, _ = self.play.engine.reviewer.activate(
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
        values = {v.target: int(v.value) for v in build.sheet.values}
        channel = next((c for c in rules.channels if c.id == command.channel_id), None)
        if channel is not None and command.kind != "cancel":
            from wayfarer.rules.recovery_types import require_settled

            require_settled(
                state.resources.recovery_tasks,
                frozenset({channel.target_id}),
                state.resources.game_time,
            )
            require_hazards_settled(
                state.resources.hazards, frozenset({channel.target_id}), state.resources.game_time
            )
        target = next(
            (a for a in state.actors if channel and a.actor_id == channel.target_id), None
        )
        target_values: dict[str, int] = {}
        if target is not None:
            target_build, _ = self.play.engine.reviewer.activate(
                target.proposal,
                target.approval,
                campaign_id=state.campaign_id,
                actor_id=target.actor_id,
            )
            target_values = {v.target: int(v.value) for v in target_build.sheet.values}
        if spec.kind in ("burning-malediction", "mind-reading") and target is None:
            raise ValidationError("Resisted ability requires an authoritative target build")
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
        incapacitated = bool(actor.conditions or hp.injury and hp.injury.incapacitated)
        if hp.injury and hp.injury.stunned and command.kind in ("activate", "maintain", "analyze"):
            raise ValidationError("Stunned actors must Do Nothing, not concentrate")
        if command.kind != "cancel" and (incapacitated and command.kind != "resolve"):
            raise ValidationError("Incapacitated actor cannot activate an ability")
        if (
            command.kind not in ("cancel", "resolve")
            and actor.available_at > state.resources.game_time
        ):
            raise ConflictError("Actor is not ready")
        if command.kind in ("activate", "analyze"):
            require_idle_concentration(state.resources, actor.actor_id)
        encounter = next(
            (
                e
                for e in state.encounters
                if e.status == "active" and actor.actor_id in e.turn_order
            ),
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
                combat = self.play.engine.combat
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
                and self.play.engine.resources.specs[i.definition_id].slot in ("hand", "hands")
            ),
        )
        if taking_turn:
            validate_binding(spec, context.level, context.options)
            validate_target(state.world, command, spec, context)
            fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
            if (
                command.kind == "activate"
                and fatigue_cost(spec) > fp.current
                and fatigue_cost(spec)
            ):
                raise ValidationError("Insufficient fatigue for ability")
            state = injury_turn(
                self.play, state, actor.actor_id, command.id, start=True, do_nothing=False
            )
            hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
            context = replace(context, unavailable=bool(hp.injury and hp.injury.incapacitated))
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(update={"revision": state.revision})
                }
            )
        resources, world, result = apply_ability(
            state.resources, state.world, command, spec, context, rng=self.play.rng, system=True
        )
        updated = state.model_copy(
            update={
                "revision": resources.revision,
                "resources": resources,
                "world": world,
                "actors": tuple(
                    a.model_copy(update={"available_at": resources.game_time + 1})
                    if a.actor_id == actor.actor_id and result.outcome == "concentrating"
                    else a
                    for a in state.actors
                ),
            }
        )
        if command.kind in ("activate", "analyze"):
            resources = resources.model_copy(
                update={
                    "recovery_tasks": interrupt_tasks(
                        resources.recovery_tasks, frozenset({actor.actor_id}), resources.game_time
                    )
                }
            )
            updated = updated.model_copy(update={"resources": resources})
        if encounter is not None and command.kind in ("activate", "analyze"):
            combat = self.play.engine.combat
            assert combat is not None
            participant = next(p for p in encounter.participants if p.actor_id == actor.actor_id)
            encounter = combat._advance(
                combat._replace(
                    encounter,
                    participant.model_copy(
                        update={"last_maneuver": "concentrate", "maneuver_state": ManeuverState()}
                    ),
                )
            )
            prior = next(e for e in state.encounters if e.id == encounter.id)
            if encounter.round > prior.round:
                resources = self.play.engine.resources.apply(
                    resources,
                    Advance(
                        id=internal_id(command.id, "round-time"),
                        actor_id=actor.actor_id,
                        expected_revision=resources.revision,
                        to=resources.game_time + encounter.round - prior.round,
                    ),
                    system=True,
                    rng=self.play.rng,
                )
                resources = resources.model_copy(update={"revision": updated.revision})
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
            updated = updated.model_copy(
                update={
                    "encounters": tuple(
                        encounter if e.id == encounter.id else e for e in state.encounters
                    )
                }
            )
        if taking_turn:
            updated = injury_turn(
                self.play, updated, actor.actor_id, command.id, start=False, do_nothing=False
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
                            update={
                                "ready_item_ids": tuple(i for i in p.ready_item_ids if i in ready)
                            }
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
                combat = self.play.engine.combat
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
        return updated

    async def execute(self, cid: str, value: object, *, principal_id: str) -> AbilityOutcome:
        command = AbilityCommand.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        service = AbilityService(play)
        state = play._load(await play.store.read(cid))
        member = CampaignAccess(play)._member(state, principal_id)
        if member.role != "player" or command.actor_id not in member.actor_ids:
            raise AuthorizationError("Ability actor is not controlled by principal")
        payload = principal_id + ":" + command.model_dump_json()

        def resolve(campaign: Campaign) -> Event:
            current = play._load(campaign)
            updated = service.reduce(current, command)
            updated = play.checkpoint(updated, before=current)
            play.commit(campaign, updated)
            return Event(input=payload, action="resource", outcome="ability", roll=None)

        committed = await play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        updated = play._load(committed["state"])
        event = next(e for e in updated.resources.events if e.id == internal_id(command.id))
        return AbilityEvent.model_validate_json(event.kind).outcome
