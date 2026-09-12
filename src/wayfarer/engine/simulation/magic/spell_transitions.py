"""Approved spell state transitions; transactions belong to the service."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import (
    Battlefield,
    CombatEngine,
    Defense,
    Encounter,
    GridPoint,
    PendingDefense,
)
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    SpellEvent,
    SpellResult,
    apply_spell,
    event_id,
    latest,
)
from wayfarer.engine.simulation.resources import Advance, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

SpellResolver = Callable[[RulesContext, PlayState, SpellCommand], SpellEnvironment]


def approved_context(
    runtime: RulesContext, state: PlayState, command: SpellCommand
) -> SpellContext:
    from wayfarer.engine.simulation.campaign.party import synchronous

    synchronous(state, command.actor_id)
    rules = runtime.rules.spells
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
        if encounter.spatial_kind == "hex":
            from wayfarer.engine.simulation.hex_geometry import Hex

            cell = runtime.require_hex(encounter).cell(
                Hex(q=command.position[0], r=command.position[1])
            )
            if cell.blocked:
                raise ValidationError("Light cannot move inside solid terrain")
        else:
            field = (
                next(
                    f for f in runtime.rules.combat.battlefields if f.id == encounter.battlefield_id
                )
                if runtime.rules.combat
                else None
            )
            if not isinstance(field, Battlefield) or not (
                0 <= command.position[0] < field.width and 0 <= command.position[1] < field.height
            ):
                raise ValidationError("Light destination is outside the battlefield")
        position = command.position
    if command.kind == "focus" and command.position is None:
        raise ValidationError("Light manipulation requires a destination")
    magic_item = None
    if channel.magic_item_id is not None:
        magic_item = next(
            item
            for item in rules.magic_items
            if item.item_id == channel.magic_item_id and item.spell_id == command.spell_id
        )
        held = next(
            (item for item in state.resources.items if item.id == channel.magic_item_id), None
        )
        if (
            held is None
            or held.owner_id != command.actor_id
            or (not held.equipped and not held.ready)
            or (held.condition is not None and held.condition.disabled)
        ):
            raise ValidationError("Caster is not holding a usable magic item")
    context = build_context(
        runtime,
        state,
        command,
        SpellEnvironment(
            target_id=channel.target_id,
            mana=channel.mana,
            distance=distance,
            radius=command.radius,
            energy=command.energy,
            magic_item=magic_item,
        ),
    )
    if channel.ceremonial:
        ht: list[tuple[str, int]] = []
        entities_by_id = {e.id: e for e in state.world.entities}
        for contribution in channel.ceremonial.contributions:
            participant = next(
                (a for a in state.actors if a.actor_id == contribution.actor_id), None
            )
            if participant is None or participant.approval is None:
                raise ValidationError("Ceremonial participant requires an approved build")
            if entities_by_id[contribution.actor_id].location_id != channel.location_id:
                raise ValidationError("Ceremonial participants must share the ritual location")
            build, _ = runtime.reviewer.activate(
                participant.proposal,
                participant.approval,
                campaign_id=state.campaign_id,
                actor_id=participant.actor_id,
            )
            purchases = {p.definition_id: p.amount for p in build.purchases}
            values = {v.target: int(v.value) for v in build.sheet.values}
            spell_skill = values.get("spell:" + command.spell_id)
            if contribution.role == "leader" and (spell_skill is None or spell_skill < 15):
                raise ValidationError("Ceremonial leader requires spell skill 15+")
            if contribution.role == "mage" and (
                spell_skill is None or spell_skill < 15 or "trait:magery-0" not in purchases
            ):
                raise ValidationError("Mage contribution requires Magery and spell skill 15+")
            if contribution.role == "nonmage" and spell_skill is None:
                raise ValidationError("Trained ceremonial assistant must know the spell")
            ht.append((contribution.actor_id, values["attribute:ht"]))
        for opponent in channel.ceremonial.opposing_spectators:
            if entities_by_id[opponent].location_id != channel.location_id:
                raise ValidationError("Ceremonial opposition must be present at the ritual")
        context = context.model_copy(
            update={"ceremonial": channel.ceremonial, "ceremonial_ht": tuple(ht)}
        )
    return context.model_copy(
        update={
            "execute_effects": True,
            "execution_version": rules.execution_version,
            "location_id": channel.location_id,
            "encounter_id": encounter.id if encounter else None,
            "position": position,
            "geometry": geometry,
            "area": channel.area,
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


def _interrupt_cast(event: ResourceEvent, command_id: str) -> ResourceEvent:
    if event.id != event_id(command_id):
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


def advance_cast_turn(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: SpellCommand,
    context: SpellContext,
    *,
    turn_started: bool = False,
) -> PlayState:
    from wayfarer.engine.simulation.actors import injury_turn

    if not turn_started:
        before_hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
        state = injury_turn(
            runtime,
            state,
            command.actor_id,
            command.id,
            start=True,
            do_nothing=bool(before_hp.injury and before_hp.injury.stunned),
        )
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    interrupted = bool(hp.injury and hp.injury.incapacitated)
    if interrupted:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "events": tuple(
                            _interrupt_cast(e, command.id) for e in state.resources.events
                        )
                    }
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
            state.resources, completed, context, rng=runtime.rng, system=True
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
        runtime, state, command.actor_id, command.id, start=False, do_nothing=interrupted
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
        resources = runtime.resources.apply(
            resources,
            Advance(
                id=event_id(command.id) + ":round",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                to=resources.game_time + updated.round - encounter.round,
            ),
            system=True,
            rng=runtime.rng,
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
    from wayfarer.engine.simulation.magic.backfires import backfires

    if result.outcome == "critical-failure" and any(
        b.cast_id == command.cast_id and b.flavor == "illusion" for b in backfires(resources)
    ):
        return result.model_copy(update={"outcome": "active", "checks": ()})
    return result


@dataclass(frozen=True)
class SpellExecutionContext:
    runtime: RulesContext
    resolver: SpellResolver | None = None


def _prepare_spell(
    before: PlayState, command: SpellCommand, execution: SpellExecutionContext
) -> tuple[SpellContext, Encounter | None]:
    runtime = execution.runtime
    guard(before, command.actor_id, "spell")
    encounter = combat_guard(before, command)
    if encounter is not None and execution.resolver is not None:
        raise ValidationError("Combat spell dispatch requires the maneuver adapter")
    context = (
        build_context(
            runtime,
            before,
            command,
            SpellEnvironment.model_validate(execution.resolver(runtime, before, command)),
        )
        if execution.resolver
        else approved_context(runtime, before, command)
    )
    if encounter is not None and context.execution_version == 2 and command.kind == "complete":
        raise ValidationError("Combat casting completes within its final Concentrate maneuver")
    if command.kind in ("release", "expand") and (
        encounter is None or execution.resolver is not None
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
        from wayfarer.engine.simulation.magic.concentration import require_idle_concentration

        require_idle_concentration(before.resources, command.actor_id)
    return context, encounter


def _release_missile(
    runtime: RulesContext,
    before: PlayState,
    updated: PlayState,
    command: SpellCommand,
    context: SpellContext,
    encounter: Encounter,
    turn_started: bool,
) -> PlayState:
    from wayfarer.engine.simulation.actors import injury_turn
    from wayfarer.engine.simulation.combat.melee import defense_value

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
            runtime, updated, command.actor_id, command.id, start=True, do_nothing=False
        )
    hp = next(p for p in updated.resources.pools if p.id == "hp:" + command.actor_id)
    if hp.injury and hp.injury.incapacitated:
        raise ValidationError("Caster cannot release the missile")
    updated = updated.model_copy(
        update={"resources": updated.resources.model_copy(update={"revision": updated.revision})}
    )

    target = next(p for p in encounter.participants if p.actor_id == context.target_id)
    if command.target_item_id:
        from wayfarer.engine.simulation.combat.objects.combat import target_modifier

        target_modifier(runtime, before, target.actor_id, command.target_item_id)
        if next(i for i in before.resources.items if i.id == command.target_item_id).ground:
            raise ValidationError("Ground spell targets require a dedicated geometry adapter")
    allowed: list[Defense] = ["none"]
    for defense in ("dodge", "block"):
        try:
            defense_value(runtime, before, target, defense)
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
                weapon_id="spell:" + hashlib.sha256(command.cast_id.encode()).hexdigest(),
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
    return updated


def reduce_spell(
    before: PlayState, command: SpellCommand, execution: SpellExecutionContext
) -> tuple[PlayState, SpellResult]:
    runtime = execution.runtime
    context, encounter = _prepare_spell(before, command, execution)
    turn_started = False
    unable_to_handle = False
    casting_resources = before.resources
    if (
        encounter is not None
        and encounter.wait_interrupt is None
        and command.kind in ("start", "concentrate", "focus", "release", "expand")
    ):
        from wayfarer.engine.simulation.magic.backfires import refund_due

        hp = next(p for p in casting_resources.pools if p.id == "hp:" + command.actor_id)
        assert hp.injury
        casting_resources = refund_due(casting_resources, command.actor_id, turn=hp.injury.turn + 1)
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
            rng=runtime.rng,
            system=True,
            validate_only=True,
        )
        if encounter.wait_interrupt is None:
            from wayfarer.engine.simulation.actors import injury_turn

            began = injury_turn(
                runtime, before, command.actor_id, command.id, start=True, do_nothing=False
            )
            casting_resources = began.resources.model_copy(update={"revision": before.revision})
            turn_started = True
            hp = next(p for p in casting_resources.pools if p.id == "hp:" + command.actor_id)
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
            casting_resources, command, context, rng=runtime.rng, system=True
        )
    updated = before.model_copy(update={"revision": resources.revision, "resources": resources})
    if encounter is not None and (
        unable_to_handle or command.kind in ("start", "concentrate", "expand", "focus")
    ):
        updated = advance_cast_turn(
            runtime, updated, encounter, command, context, turn_started=turn_started
        )
    if encounter is not None and command.kind == "release" and not unable_to_handle:
        updated = _release_missile(
            runtime, before, updated, command, context, encounter, turn_started
        )
    return updated, _recorded_spell_result(updated, command)


def _recorded_spell_result(state: PlayState, command: SpellCommand) -> SpellResult:
    # A final Concentrate maneuver may replace the initial casting result.
    return SpellEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == event_id(command.id))
    ).result
