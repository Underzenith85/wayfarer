"""Approved spell transactions on the existing campaign ledger; no frozen v1 route."""

import hashlib
import json
from collections.abc import Callable

from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import guard
from wayfarer.orchestration.spell_bindings import SpellEnvironment
from wayfarer.orchestration.spell_bindings import approved_context as build_context
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, Defense, Encounter, GridPoint, PendingDefense
from wayfarer.simulation.maneuvers import ManeuverState
from wayfarer.simulation.resources import Advance, ResourceEvent
from wayfarer.simulation.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    SpellEvent,
    SpellResult,
    apply_spell,
    event_id,
)

SpellResolver = Callable[[PlayService, PlayState, SpellCommand], SpellEnvironment]


def approved_context(play: PlayService, state: PlayState, command: SpellCommand) -> SpellContext:
    from wayfarer.simulation.party import synchronous

    synchronous(state, command.actor_id)
    rules = play.engine.rules.spells
    if rules is None:
        raise ValidationError("Campaign has no executable spell bindings")
    channel = next((c for c in rules.channels if c.id == command.channel_id), None)
    if channel is None or (channel.actor_id, channel.spell_id) != (
        command.actor_id,
        command.spell_id,
    ):
        raise AuthorizationError("Spell channel does not authorize this actor and spell")
    entities = {e.id: e for e in state.world.entities}
    if command.kind != "cancel" and any(
        entities[e].location_id != channel.location_id
        for e in (command.actor_id, channel.target_id)
    ):
        raise ValidationError("Spell channel location changed")
    encounter = next(
        (e for e in state.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    position = None
    distance = channel.distance_yards
    if encounter:
        positions = {p.actor_id: p.position for p in encounter.participants}
        if channel.target_id not in positions:
            raise ValidationError("Spell target is outside the encounter")
        point = positions[channel.target_id]
        if not isinstance(point, GridPoint):
            raise ValidationError("Spell bindings require supported square combat placements")
        position = (point.x, point.y)
        distance = CombatEngine.distance(positions[command.actor_id], point)
    elif command.spell_id in ("create-fire", "fireball"):
        raise ValidationError("Area and missile spells require authoritative combat placements")
    context = build_context(
        play,
        state,
        command,
        SpellEnvironment(
            target_id=channel.target_id,
            mana=channel.mana,
            distance=distance,
            radius=command.radius,
            energy=command.energy,
        ),
    )
    return context.model_copy(
        update={
            "execute_effects": True,
            "location_id": channel.location_id,
            "encounter_id": encounter.id if encounter else None,
            "position": position,
        }
    )


def combat_guard(state: PlayState, command: SpellCommand) -> Encounter | None:
    encounter = next(
        (e for e in state.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    if encounter is None:
        return None
    if (
        encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason
        or encounter.current_actor_id != command.actor_id
    ):
        raise ConflictError("Spell must obey the encounter turn and defense pause")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if actor.forced_do_nothing and command.kind != "cancel":
        raise ValidationError("Actor must take the required Do Nothing maneuver")
    if command.kind == "release" and any(
        p.maneuver_state.wait
        and p.maneuver_state.wait.actor_id == command.actor_id
        and p.maneuver_state.wait.action == "attack"
        for p in encounter.participants
    ):
        raise ConflictError("Missile release cannot bypass a declared Wait")
    return encounter


def advance_cast_turn(
    play: PlayService, state: PlayState, encounter: Encounter, command: SpellCommand
) -> PlayState:
    from wayfarer.orchestration.gurps_melee import injury_turn

    state = injury_turn(play, state, command.actor_id, command.id, start=True, do_nothing=False)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    interrupted = bool(hp.injury and hp.injury.incapacitated)
    if interrupted:

        def interrupt(event: ResourceEvent) -> ResourceEvent:
            if event.id != event_id(command.id):
                return event
            record = SpellEvent.model_validate_json(event.kind)
            return event.model_copy(
                update={
                    "kind": record.model_copy(
                        update={
                            "effect": record.effect.model_copy(update={"phase": "ended"}),
                            "result": SpellResult(outcome="interrupted"),
                        }
                    ).model_dump_json()
                }
            )

        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"events": tuple(interrupt(e) for e in state.resources.events)}
                )
            }
        )
    state = injury_turn(
        play, state, command.actor_id, command.id, start=False, do_nothing=interrupted
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"revision": state.revision})}
    )
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    updated = CombatEngine._advance(
        CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "last_maneuver": "do_nothing" if interrupted else "concentrate",
                    "maneuver_state": ManeuverState(),
                }
            ),
        )
    )
    resources = state.resources
    if updated.round > encounter.round:
        resources = play.engine.resources.apply(
            resources,
            Advance(
                id=event_id(command.id) + ":round",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                to=resources.game_time + updated.round - encounter.round,
            ),
            system=True,
        ).model_copy(update={"revision": state.revision})
    return state.model_copy(
        update={
            "resources": resources,
            "encounters": tuple(updated if e.id == encounter.id else e for e in state.encounters),
            "party": state.party.model_copy(
                update={
                    "groups": tuple(
                        g.model_copy(update={"ready_through": resources.game_time})
                        for g in state.party.groups
                    )
                }
            ),
        }
    )


class SpellService:
    """An internal transaction seam, not permission to invent spell bindings."""

    def __init__(self, play: PlayService, resolve: SpellResolver | None = None) -> None:
        self.play, self.resolve = play, resolve

    async def execute(
        self,
        cid: str,
        value: object,
        *,
        authenticated_gm_id: str | None = None,
        principal_id: str | None = None,
    ) -> SpellResult:
        command = SpellCommand.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        if (principal_id is None) == (authenticated_gm_id is None):
            raise AuthorizationError("Select exactly one authenticated spell principal")
        identity = principal_id or authenticated_gm_id
        assert identity is not None
        member = CampaignAccess(play)._member(state, identity)
        if principal_id is not None:
            if (
                self.resolve is not None
                or member.role != "player"
                or command.actor_id not in member.actor_ids
            ):
                raise AuthorizationError("Spell actor is not controlled by principal")
        elif member.role != "gm" or identity not in play.engine.reviewer.gm_ids:
            raise ValidationError("Spell lifecycle requires trusted director authority")
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Spell lifecycle requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-lifecycle",
                "principal_id": identity,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> Event:
            before = play._load(campaign)
            guard(before, command.actor_id, "spell")
            encounter = combat_guard(before, command)
            if encounter is not None and self.resolve is not None:
                raise ValidationError("Combat spell dispatch requires the maneuver adapter")
            context = (
                build_context(
                    play,
                    before,
                    command,
                    SpellEnvironment.model_validate(self.resolve(play, before, command)),
                )
                if self.resolve
                else approved_context(play, before, command)
            )
            if command.kind in ("release", "expand") and (
                encounter is None or self.resolve is not None
            ):
                raise ValidationError("Missile commands require approved combat dispatch")
            if (
                command.kind in ("release", "expand")
                and next(
                    p.current for p in before.resources.pools if p.id == "hp:" + command.actor_id
                )
                <= 0
            ):
                raise ValidationError(
                    "Missile handling at nonpositive HP requires the held-missile injury adapter"
                )
            if command.kind == "release" and context.distance > 50:
                raise ValidationError("Fireball exceeds its maximum range")
            if context.profile_id != PROFILE:
                raise ValidationError("Spell context does not match campaign profile")
            if command.actor_id not in {a.actor_id for a in before.actors}:
                raise ValidationError("Unknown caster")
            perceived = {e.id for e in before.world.perspective(command.actor_id).entities}
            if context.target_id != command.actor_id and context.target_id not in perceived:
                raise ValidationError("Spell target is not perceived")
            if command.kind == "start":
                from wayfarer.simulation.concentration import require_idle_concentration

                require_idle_concentration(before.resources, command.actor_id)
            resources, result = apply_spell(
                before.resources, command, context, rng=play.rng, system=True
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            if encounter is not None and command.kind in ("start", "concentrate", "expand"):
                updated = advance_cast_turn(play, updated, encounter, command)
            if encounter is not None and command.kind == "release":
                from wayfarer.orchestration.gurps_melee import defense_value, injury_turn

                if context.target_id == command.actor_id:
                    raise ValidationError("Missile release requires another participant")
                updated = injury_turn(
                    play, updated, command.actor_id, command.id, start=True, do_nothing=False
                )
                hp = next(p for p in updated.resources.pools if p.id == "hp:" + command.actor_id)
                if hp.injury and hp.injury.incapacitated:
                    raise ValidationError("Caster cannot release the missile")
                updated = updated.model_copy(
                    update={
                        "resources": updated.resources.model_copy(
                            update={"revision": updated.revision}
                        )
                    }
                )

                target = next(p for p in encounter.participants if p.actor_id == context.target_id)
                allowed: list[Defense] = ["none"]
                for defense in ("dodge", "block"):
                    try:
                        defense_value(play, before, target, defense)
                    except ValidationError:
                        continue
                    allowed.append(defense)
                attacker = next(p for p in encounter.participants if p.actor_id == command.actor_id)
                encounter = CombatEngine._replace(
                    encounter,
                    attacker.model_copy(
                        update={
                            "last_maneuver": "attack",
                            "maneuver_state": ManeuverState(),
                        }
                    ),
                ).model_copy(
                    update={
                        "pending_defense": PendingDefense(
                            id=event_id(command.id),
                            attacker_id=command.actor_id,
                            defender_id=context.target_id,
                            weapon_id="spell:"
                            + hashlib.sha256(command.cast_id.encode()).hexdigest(),
                            allowed=tuple(allowed),
                            opened_round=encounter.round,
                            opened_turn=encounter.turn_index,
                            spell_cast_id=command.cast_id,
                        )
                    }
                )
                updated = updated.model_copy(
                    update={
                        "encounters": tuple(
                            encounter if e.id == encounter.id else e for e in updated.encounters
                        )
                    }
                )
            updated = play.checkpoint(updated, before=before)
            play.engine.validate(updated)
            result = SpellEvent.model_validate_json(
                next(e.kind for e in updated.resources.events if e.id == event_id(command.id))
            ).result
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            # Roll targets, opposed traces, and bindings stay in the private ledger.
            return Event(
                input=payload, action="resource", outcome="spell:" + result.outcome, roll=None
            )

        committed = await play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=identity,
        )
        saved = play._load(committed["state"])
        recorded = next(e for e in saved.resources.events if e.id == event_id(command.id))
        return SpellEvent.model_validate_json(recorded.kind).result
