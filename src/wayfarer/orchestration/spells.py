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
from wayfarer.simulation.resources import Advance, ResourceEvent, ResourceState
from wayfarer.simulation.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    SpellEvent,
    SpellResult,
    apply_spell,
    event_id,
    latest,
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
    geometry = "square"
    distance = channel.distance_yards
    if encounter:
        positions = {p.actor_id: p.position for p in encounter.participants}
        if channel.target_id not in positions:
            raise ValidationError("Spell target is outside the encounter")
        point = positions[channel.target_id]
        if isinstance(point, GridPoint):
            position = (point.x, point.y)
        else:
            position = (point.q, point.r)
            geometry = "hex"
        distance = CombatEngine.distance(positions[command.actor_id], point)
    elif command.spell_id in ("create-fire", "fireball"):
        raise ValidationError("Area and missile spells require authoritative combat placements")
    if command.position is not None:
        if command.kind != "focus" or encounter is None:
            raise ValidationError("A manipulation destination requires combat concentration")
        if encounter.hex_battlefield is not None:
            from wayfarer.simulation.hex_geometry import Hex

            cell = encounter.hex_battlefield.cell(Hex(q=command.position[0], r=command.position[1]))
            if cell.blocked:
                raise ValidationError("Light cannot move inside solid terrain")
        else:
            field = (
                next(
                    f
                    for f in play.engine.rules.combat.battlefields
                    if f.id == encounter.battlefield_id
                )
                if play.engine.rules.combat
                else None
            )
            if field is None or not (
                0 <= command.position[0] < field.width and 0 <= command.position[1] < field.height
            ):
                raise ValidationError("Light destination is outside the battlefield")
        position = command.position
    if command.kind == "focus" and command.position is None:
        raise ValidationError("Light manipulation requires a destination")
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
            "execution_version": rules.execution_version,
            "location_id": channel.location_id,
            "encounter_id": encounter.id if encounter else None,
            "position": position,
            "geometry": geometry,
            "light_radius": channel.light_radius,
            "light_penalty": channel.light_penalty,
        }
    )


def combat_guard(state: PlayState, command: SpellCommand) -> Encounter | None:
    encounter = next(
        (e for e in state.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    if encounter is None:
        return None
    interrupt = encounter.wait_interrupt
    reaction = bool(
        interrupt
        and not interrupt.ready
        and not interrupt.reacting
        and interrupt.waiter_id == command.actor_id
        and command.kind == "release"
        and interrupt.declaration.reaction == "attack"
        and interrupt.declaration.item_id
        == "spell:" + hashlib.sha256(command.cast_id.encode()).hexdigest()
    )
    if (
        encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or (encounter.wait_interrupt is not None and not reaction)
        or encounter.blocked_reason
        or (encounter.current_actor_id != command.actor_id and not reaction)
    ):
        raise ConflictError("Spell must obey the encounter turn and defense pause")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if actor.forced_do_nothing and command.kind != "cancel":
        raise ValidationError("Actor must take the required Do Nothing maneuver")
    if (
        command.kind == "release"
        and not reaction
        and any(
            p.maneuver_state.wait
            and p.maneuver_state.wait.actor_id == command.actor_id
            and p.maneuver_state.wait.action == "attack"
            for p in encounter.participants
        )
    ):
        raise ConflictError("Missile release cannot bypass a declared Wait")
    return encounter


def advance_cast_turn(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: SpellCommand,
    context: SpellContext,
    *,
    turn_started: bool = False,
) -> PlayState:
    from wayfarer.orchestration.gurps_melee import injury_turn

    if not turn_started:
        before_hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
        state = injury_turn(
            play,
            state,
            command.actor_id,
            command.id,
            start=True,
            do_nothing=bool(before_hp.injury and before_hp.injury.stunned),
        )
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
                            "effect": record.effect.model_copy(update={"phase": "ended"})
                            if record.effect.phase == "casting"
                            else record.effect,
                            "result": record.result.model_copy(update={"outcome": "interrupted"}),
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
    effect = latest(state.resources).get(command.cast_id)
    if (
        not interrupted
        and effect is not None
        and effect.execution_version == 2
        and effect.phase == "casting"
        and effect.required_turns == effect.concentration_seconds
    ):
        completed = command.model_copy(
            update={
                "id": "complete:" + hashlib.sha256(command.id.encode()).hexdigest(),
                "kind": "complete",
                "expected_revision": state.resources.revision,
                "hp_energy": 0,
            }
        )
        resources, result = apply_spell(
            state.resources, completed, context, rng=play.rng, system=True
        )
        completed_effect = latest(resources)[command.cast_id]
        events = []
        for event in resources.events:
            if event.id == event_id(command.id):
                record = SpellEvent.model_validate_json(event.kind)
                event = event.model_copy(
                    update={
                        "kind": record.model_copy(
                            update={
                                "effect": completed_effect,
                                "result": result,
                            }
                        ).model_dump_json()
                    }
                )
            events.append(event)
        state = state.model_copy(
            update={
                "resources": resources.model_copy(
                    update={
                        "events": tuple(events),
                        "revision": state.revision,
                    }
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
            rng=play.rng,
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


def apparent_result(
    resources: ResourceState, command: SpellCommand, result: SpellResult
) -> SpellResult:
    from wayfarer.simulation.spell_backfires import backfires

    if result.outcome == "critical-failure" and any(
        b.cast_id == command.cast_id and b.flavor == "illusion" for b in backfires(resources)
    ):
        return result.model_copy(update={"outcome": "active", "checks": ()})
    return result


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
        if command.target_item_id is not None and command.kind != "release":
            raise ValidationError("Object targeting requires a missile release")
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
            if (
                encounter is not None
                and context.execution_version == 2
                and command.kind == "complete"
            ):
                raise ValidationError(
                    "Combat casting completes within its final Concentrate maneuver"
                )
            if command.kind in ("release", "expand") and (
                encounter is None or self.resolve is not None
            ):
                raise ValidationError("Missile commands require approved combat dispatch")
            if command.kind == "release" and context.distance > 50:
                raise ValidationError("Fireball exceeds its maximum range")
            if context.profile_id != PROFILE:
                raise ValidationError("Spell context does not match campaign profile")
            if command.actor_id not in {a.actor_id for a in before.actors}:
                raise ValidationError("Unknown caster")
            perceived = {e.id for e in before.world.perspective(command.actor_id).entities}
            if (
                command.kind not in ("cancel", "remember")
                and context.target_id != command.actor_id
                and context.target_id not in perceived
            ):
                raise ValidationError("Spell target is not perceived")
            if command.kind == "start":
                from wayfarer.simulation.concentration import require_idle_concentration

                require_idle_concentration(before.resources, command.actor_id)
            turn_started = False
            unable_to_handle = False
            casting_resources = before.resources
            if (
                encounter is not None
                and encounter.wait_interrupt is None
                and command.kind in ("start", "concentrate", "focus", "release", "expand")
            ):
                from wayfarer.simulation.spell_backfires import refund_due

                hp = next(p for p in casting_resources.pools if p.id == "hp:" + command.actor_id)
                assert hp.injury
                casting_resources = refund_due(
                    casting_resources, command.actor_id, turn=hp.injury.turn + 1
                )
            if encounter is not None and command.kind in ("release", "expand"):
                if command.kind == "release" and context.target_id == command.actor_id:
                    raise ValidationError("Missile release requires another participant")
                if (
                    encounter.wait_interrupt is not None
                    and encounter.wait_interrupt.declaration.reaction_target_id != context.target_id
                ):
                    raise ValidationError("Missile Wait target differs from its declaration")
                apply_spell(
                    casting_resources,
                    command,
                    context,
                    rng=play.rng,
                    system=True,
                    validate_only=True,
                )
                if encounter.wait_interrupt is None:
                    from wayfarer.orchestration.gurps_melee import injury_turn

                    began = injury_turn(
                        play, before, command.actor_id, command.id, start=True, do_nothing=False
                    )
                    casting_resources = began.resources.model_copy(
                        update={"revision": before.revision}
                    )
                    turn_started = True
                    hp = next(
                        p for p in casting_resources.pools if p.id == "hp:" + command.actor_id
                    )
                    unable_to_handle = bool(hp.injury and hp.injury.incapacitated)
            if unable_to_handle:
                effect = latest(casting_resources)[command.cast_id]
                result = SpellResult(outcome="interrupted")
                resources = casting_resources.model_copy(
                    update={
                        "revision": before.revision + 1,
                        "events": casting_resources.events
                        + (
                            ResourceEvent(
                                id=event_id(command.id),
                                at=casting_resources.game_time,
                                target_id=command.actor_id,
                                kind=SpellEvent(effect=effect, result=result).model_dump_json(),
                            ),
                        ),
                    }
                )
            else:
                resources, result = apply_spell(
                    casting_resources, command, context, rng=play.rng, system=True
                )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            if encounter is not None and (
                unable_to_handle or command.kind in ("start", "concentrate", "expand", "focus")
            ):
                updated = advance_cast_turn(
                    play, updated, encounter, command, context, turn_started=turn_started
                )
            if encounter is not None and command.kind == "release" and not unable_to_handle:
                from wayfarer.orchestration.gurps_melee import defense_value, injury_turn

                if context.target_id == command.actor_id:
                    raise ValidationError("Missile release requires another participant")
                if encounter.wait_interrupt is not None:
                    interrupt = encounter.wait_interrupt
                    if interrupt.declaration.reaction_target_id != context.target_id:
                        raise ValidationError("Missile Wait target differs from its declaration")
                    encounter = encounter.model_copy(
                        update={
                            "turn_index": encounter.turn_order.index(command.actor_id),
                            "wait_interrupt": interrupt.model_copy(update={"reacting": True}),
                        }
                    )
                elif not turn_started:
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
                if command.target_item_id:
                    from wayfarer.orchestration.object_combat import target_modifier

                    target_modifier(play, before, target.actor_id, command.target_item_id)
                    if next(
                        i for i in before.resources.items if i.id == command.target_item_id
                    ).ground:
                        raise ValidationError(
                            "Ground spell targets require a dedicated geometry adapter"
                        )
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
                            target_item_id=command.target_item_id,
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
            play.commit(campaign, updated)
            result = SpellEvent.model_validate_json(
                next(e.kind for e in updated.resources.events if e.id == event_id(command.id))
            ).result
            # Roll targets, opposed traces, and bindings stay in the private ledger.
            return Event(
                input=payload,
                action="resource",
                outcome="spell:" + apparent_result(updated.resources, command, result).outcome,
                roll=None,
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
        result = SpellEvent.model_validate_json(recorded.kind).result
        return (
            apparent_result(saved.resources, command, result)
            if principal_id is not None
            else result
        )
