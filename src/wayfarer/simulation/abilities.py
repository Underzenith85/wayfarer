"""Executable supernatural effects on the existing resource/knowledge ledgers."""

import hashlib
from dataclasses import dataclass

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.abilities import PROFILE, fatigue_cost, range_penalty, validate_binding
from wayfarer.rules.checks import CheckTrace, Modifier, Outcome, RandomSource
from wayfarer.rules.gurps_checks import Contestant, resistance_roll, success_roll
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.ability_types import (
    AbilityChannel,
    AbilityCommand,
    AbilityEffect,
    AbilityEvent,
    AbilityOutcome,
    AbilityOutcomeKind,
    AbilitySpec,
)
from wayfarer.simulation.condition_checks import check_modifiers, retching_penalty
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import ResourceEvent, ResourceState
from wayfarer.world import World

PREFIX = "ability:"


def internal_id(command_id: str, purpose: str = "result") -> str:
    return PREFIX + hashlib.sha256((purpose + ":" + command_id).encode()).hexdigest()


def record(resources: ResourceState, command: AbilityCommand, event: AbilityEvent) -> ResourceState:
    return resources.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "events": resources.events
            + (
                ResourceEvent(
                    id=internal_id(command.id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    )


def history(resources: ResourceState) -> tuple[tuple[int, AbilityEvent], ...]:
    return tuple(
        (e.at, AbilityEvent.model_validate_json(e.kind))
        for e in resources.events
        if e.id.startswith(PREFIX)
    )


def effects(resources: ResourceState) -> tuple[AbilityEffect, ...]:
    latest: dict[tuple[str, str], AbilityEffect] = {}
    for _, event in history(resources):
        if event.effect:
            latest[event.actor_id, event.ability_id] = event.effect
    return tuple(
        e
        for e in latest.values()
        if e.active and (e.expires_at is None or resources.game_time < e.expires_at)
    )


def damage_resistance(
    resources: ResourceState, actor_id: str, *, build_revision: str | None = None
) -> int:
    """Combat adapters consume the same expiring defense, never a second DR state."""
    return sum(
        e.level
        for e in effects(resources)
        if e.actor_id == actor_id
        and e.kind == "damage-resistance"
        and not e.concentrating
        and (build_revision is None or e.build_revision == build_revision)
    )


def interrupt_concentration(
    resources: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    """Other maneuvers abandon concentration; an active defense needs Will-3."""
    from wayfarer.simulation.spells import interrupt_spells

    resources = interrupt_spells(resources, actor_id, command_id, distraction=distraction)
    additions = []
    for effect in effects(resources):
        if effect.actor_id != actor_id or not effect.concentrating:
            continue
        changed = effect.model_copy(
            update={"distracted": True} if distraction else {"active": False}
        )
        event = AbilityEvent(
            actor_id=actor_id,
            ability_id=effect.ability_id,
            target_id=effect.target_id,
            effect=changed,
            outcome=AbilityOutcome(outcome="concentrating" if distraction else "interrupted"),
        )
        additions.append(
            ResourceEvent(
                id=internal_id(command_id, "interrupt:" + effect.ability_id),
                at=resources.game_time,
                target_id=actor_id,
                kind=event.model_dump_json(),
            )
        )
    return resources.model_copy(update={"events": resources.events + tuple(additions)})


@dataclass(frozen=True)
class AbilityContext:
    profile_id: str
    level: int
    options: TraitOptions
    iq: int
    will: int
    per: int
    ht: int
    target_will: int = 10
    target_ht: int = 10
    mind_shield: int = 0
    channel: AbilityChannel | None = None
    interrupted: bool = False
    unavailable: bool = False
    shock: int = 0
    build_revision: str = ""
    held_item_ids: tuple[str, ...] = ()


def validate_target(
    world: World, command: AbilityCommand, spec: AbilitySpec, context: AbilityContext
) -> str:
    if spec.kind == "damage-resistance":
        if command.channel_id is not None:
            raise ValidationError("Defense targets only its owner")
        return command.actor_id
    channel = context.channel
    if (
        channel is None
        or channel.id != command.channel_id
        or channel.actor_id != command.actor_id
        or channel.ability_id != spec.definition_id
    ):
        raise ValidationError("Ability target unavailable")
    entities = {e.id: e for e in world.entities}
    if (
        command.actor_id not in entities
        or channel.target_id not in entities
        or entities[command.actor_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
    ):
        raise ValidationError("Authored ability range context changed")
    if spec.kind != "detect" and channel.target_id not in {
        e.id for e in world.perspective(command.actor_id).entities
    }:
        raise ValidationError("Target is not perceived")
    if spec.kind == "mind-reading" and channel.digital_mind:
        raise ValidationError("Digital minds require unsupported Cybernetic enhancement")
    if spec.kind == "mind-reading" and not channel.shared_language:
        raise ValidationError("Language-independent thought reading requires Universal")
    if "telepathic" in spec.modifiers and channel.psionic_blocked:
        raise ValidationError("Telepathic ability is suppressed")
    return channel.target_id


def apply_ability(
    resources: ResourceState,
    world: World,
    command: AbilityCommand,
    spec: AbilitySpec,
    context: AbilityContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, World, AbilityOutcome]:
    if not system or context.profile_id != PROFILE:
        raise ValidationError("Ability execution requires exact Basic Set authority")
    if resources.revision != command.expected_revision:
        raise ConflictError("Ability revision changed")
    if command.kind in ("activate", "analyze"):
        from wayfarer.simulation.concentration import require_idle_concentration

        require_idle_concentration(resources, command.actor_id)
    validate_binding(spec, context.level, context.options)
    if command.ability_id != spec.definition_id:
        raise ValidationError("Ability binding changed")
    old = next(
        (
            e
            for e in effects(resources)
            if e.actor_id == command.actor_id and e.ability_id == command.ability_id
        ),
        None,
    )
    if old is None and command.kind == "maintain":
        last = next(
            (
                event.effect
                for _, event in reversed(history(resources))
                if event.actor_id == command.actor_id
                and event.ability_id == command.ability_id
                and event.effect is not None
            ),
            None,
        )
        if last is not None and last.active and last.expires_at == resources.game_time:
            old = last
    target_id = (
        old.target_id
        if command.kind == "cancel" and old
        else validate_target(world, command, spec, context)
    )
    if (
        spec.kind == "mind-reading"
        and command.kind == "activate"
        and any(
            event.actor_id == command.actor_id
            and event.ability_id == command.ability_id
            and event.target_id == target_id
            and event.critical_failure
            and at + 86400 > resources.game_time
            for at, event in history(resources)
        )
    ):
        raise ConflictError("Mind Reading retry blocked for 24 hours")
    active: AbilityEffect | None = None
    failed = critical = False
    checks: list[CheckTrace] = []
    damage_dice: tuple[int, ...] = ()
    revealed: tuple[str, ...] = ()
    cost = fatigue_cost(spec)
    if context.unavailable and command.kind != "cancel":
        result = AbilityOutcome(outcome="unavailable")
        event = AbilityEvent(
            actor_id=command.actor_id,
            ability_id=command.ability_id,
            target_id=target_id,
            outcome=result,
            effect=old.model_copy(update={"active": False}) if old else None,
        )
        return record(resources, command, event), world, result
    fp = next((p for p in resources.pools if p.id == f"fp:{command.actor_id}"), None)
    if command.kind != "cancel" and fp is not None and fp.fatigue is not None:
        from wayfarer.simulation.fatigue import ContinueExertion, apply_fatigue

        if fp.fatigue.collapsed or fp.fatigue.unconscious or fp.fatigue.heart_attack:
            result = AbilityOutcome(outcome="unavailable")
            event = AbilityEvent(
                actor_id=command.actor_id,
                ability_id=command.ability_id,
                target_id=target_id,
                outcome=result,
            )
            return record(resources, command, event), world, result
        if fp.current <= 0 and command.kind in ("activate", "analyze", "maintain"):
            resources, exertion = apply_fatigue(
                resources,
                ContinueExertion(
                    id=internal_id(command.id, "exertion"),
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                ),
                ht=context.ht,
                will=context.will,
                rng=rng,
                system=True,
            )
            if not exertion.allowed:
                result = AbilityOutcome(outcome="unavailable")
                event = AbilityEvent(
                    actor_id=command.actor_id,
                    ability_id=command.ability_id,
                    target_id=target_id,
                    outcome=result,
                    checks=exertion.checks,
                )
                return record(resources, command, event), world, result
    if command.kind == "resolve":
        if old is None or not old.concentrating or old.channel_id != command.channel_id:
            raise ConflictError("No matching concentration to resolve")
        if resources.game_time < old.ready_at:
            raise ConflictError("Concentration is not complete")
        if old.build_revision != context.build_revision:
            raise ConflictError("Concentrating character build changed")
        cost = 0
    if command.kind in ("cancel", "maintain"):
        if old is None:
            raise ConflictError("No active ability to change")
        if command.kind == "cancel":
            active = old.model_copy(update={"active": False})
            cost = 0
        else:
            if old.channel_id != command.channel_id:
                raise ConflictError("Maintenance cannot change the active ability target")
            if old.build_revision != context.build_revision:
                raise ConflictError("Active character build changed")
            if not old.maintenance_cost or old.expires_at is None:
                raise ValidationError("This ability needs no paid maintenance")
            if resources.game_time < old.expires_at - 1:
                raise ConflictError("Maintenance is not yet due")
            cost = old.maintenance_cost
            active = old.model_copy(update={"expires_at": old.expires_at + 60})
    elif command.kind == "analyze":
        if old is not None:
            raise ConflictError("Finish or cancel existing concentration before analyzing")
        if spec.kind != "detect" or "vague" in spec.modifiers:
            raise ValidationError("This ability cannot analyze")
        if not any(
            e.actor_id == command.actor_id
            and e.ability_id == command.ability_id
            and e.target_id == target_id
            and e.outcome.outcome == "detected"
            for _, e in history(resources)
        ):
            raise ValidationError("Detect the subject before analyzing")
        cost = 0
    elif old is not None and command.kind != "resolve":
        if spec.kind != "mind-reading" or old.target_id == target_id:
            raise ConflictError("Ability already active")
    if cost:
        fp = next((p for p in resources.pools if p.id == f"fp:{command.actor_id}"), None)
        if fp is None or fp.current < cost:
            raise ValidationError("Insufficient fatigue for ability")
        from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue

        resources, _ = apply_fatigue(
            resources,
            FatigueCost(
                id=internal_id(command.id, "fp"),
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                amount=cost,
                cause="ordinary",
                power=True,
            ),
            ht=context.ht,
            rng=rng,
            system=True,
        )
    channel = context.channel
    interrupted = context.interrupted or bool(retching_penalty(resources, command.actor_id))
    if command.kind == "resolve" and old is not None and not interrupted:
        if old.distracted or (
            next(p.current for p in resources.pools if p.id == f"hp:{command.actor_id}")
            < old.hp_at_start
        ):
            distraction = success_roll(
                PROFILE,
                context.will - 3,
                check_modifiers(resources, command.actor_id, "will"),
                rng=rng,
            )
            checks.append(distraction)
            interrupted = not distraction.outcome.succeeded
    outcome: AbilityOutcomeKind
    analyzing = command.kind == "analyze" or bool(
        command.kind == "resolve" and old and old.analyzing
    )
    if command.kind in ("activate", "analyze") and spec.kind != "damage-resistance":
        hp = next(p.current for p in resources.pools if p.id == f"hp:{command.actor_id}")
        active = AbilityEffect(
            actor_id=command.actor_id,
            ability_id=command.ability_id,
            kind=spec.kind,
            target_id=target_id,
            started_at=resources.game_time,
            level=context.level,
            maintenance_cost=(cost + 1) // 2,
            concentrating=True,
            analyzing=analyzing,
            activation_shock=context.shock,
            build_revision=context.build_revision,
            ready_at=resources.game_time + 1,
            hp_at_start=hp,
            channel_id=command.channel_id,
        )
        outcome = "concentrating"
    elif command.kind == "resolve" and old is not None and interrupted:
        active = old.model_copy(update={"active": False})
        outcome = "interrupted"
    elif command.kind == "cancel":
        outcome = "cancelled"
    elif command.kind == "maintain":
        outcome = "active"
    elif spec.kind == "damage-resistance":
        outcome = "active"
    elif spec.kind == "burning-malediction":
        assert channel is not None
        trace = resistance_roll(
            PROFILE,
            Contestant(
                command.actor_id,
                context.will - channel.distance_yards,
                check_modifiers(resources, command.actor_id, "will"),
            ),
            Contestant(
                target_id,
                context.target_will,
                check_modifiers(resources, target_id, "will", defensive=True),
            ),
            rng=rng,
            rule_of_16=True,
        )
        failed, critical = not trace.affected, trace.attacker.outcome is Outcome.CRITICAL_FAILURE
        checks.extend((trace.attacker, trace.resister))
        outcome = "resisted" if failed else "hit"
        if not failed:
            damage_dice = tuple(rng.randbelow(6) + 1 for _ in range(context.level))
            damage = sum(damage_dice)
            resources, _ = apply_injury(
                resources,
                Wound(
                    id=internal_id(command.id, "injury"),
                    actor_id=target_id,
                    expected_revision=resources.revision,
                    basic_damage=damage,
                    resistance=0,
                    damage_type="burn",
                ),
                ht=context.target_ht,
                rng=rng,
                system=True,
                held_item_ids=context.held_item_ids,
            )
    elif spec.kind == "mind-reading":
        assert channel is not None
        attempts = [
            (at, e)
            for at, e in history(resources)
            if e.actor_id == command.actor_id
            and e.ability_id == command.ability_id
            and e.target_id == target_id
            and e.failed
        ]
        if any(e.critical_failure and at + 86400 > resources.game_time for at, e in attempts):
            raise ConflictError("Mind Reading retry blocked for 24 hours")
        penalty = -2 * sum(at + 3600 > resources.game_time for at, _ in attempts)
        trace = resistance_roll(
            PROFILE,
            Contestant(
                command.actor_id,
                context.iq
                - (old.activation_shock if old else context.shock)
                + range_penalty(channel.distance_yards)
                + penalty,
                check_modifiers(resources, command.actor_id, "iq"),
            ),
            Contestant(
                target_id,
                context.target_will
                + (context.mind_shield if "telepathic" in spec.modifiers else 0),
                check_modifiers(resources, target_id, "will", defensive=True),
            ),
            rng=rng,
            rule_of_16=True,
        )
        failed, critical = not trace.affected, trace.attacker.outcome is Outcome.CRITICAL_FAILURE
        checks.extend((trace.attacker, trace.resister))
        outcome = "resisted" if failed else "active"
        if not failed:
            if not channel.shared_language:
                raise ValidationError("Language-independent thought reading requires Universal")
            revealed = channel.thought_fact_ids
    else:
        assert channel is not None
        check = success_roll(
            PROFILE,
            context.iq - (old.activation_shock if old else context.shock)
            if analyzing
            else context.per,
            (
                ()
                if analyzing
                else (
                    Modifier(
                        range_penalty(channel.distance_yards), "range", spec.definition_id, "1"
                    ),
                )
            )
            + check_modifiers(resources, command.actor_id, "iq"),
            rng=rng,
        )
        failed, critical = not check.outcome.succeeded, check.outcome is Outcome.CRITICAL_FAILURE
        checks.append(check)
        outcome = "nothing" if failed else "analyzed" if analyzing else "detected"
        if not failed:
            if analyzing:
                revealed = channel.analysis_fact_ids
            else:
                revealed = channel.presence_fact_ids
                if "vague" not in spec.modifiers or check.outcome is Outcome.CRITICAL_SUCCESS:
                    revealed += channel.detection_fact_ids
                if "precise" in spec.modifiers:
                    revealed += channel.precise_fact_ids
    if outcome == "active" and active is None:
        active = AbilityEffect(
            actor_id=command.actor_id,
            ability_id=command.ability_id,
            kind=spec.kind,
            target_id=target_id,
            started_at=resources.game_time,
            expires_at=resources.game_time + 60 if fatigue_cost(spec) else None,
            level=context.level,
            maintenance_cost=(fatigue_cost(spec) + 1) // 2,
            build_revision=context.build_revision,
            channel_id=command.channel_id,
        )
    if failed and old is not None and spec.kind == "mind-reading":
        active = old.model_copy(update={"active": False})
    if command.kind == "resolve" and active is None and old is not None:
        active = old.model_copy(update={"active": False})
    for fact_id in revealed:
        world = world.learn(command.actor_id, fact_id)
    result = AbilityOutcome(outcome=outcome, revealed_fact_ids=revealed)
    event = AbilityEvent(
        actor_id=command.actor_id,
        ability_id=command.ability_id,
        target_id=target_id,
        outcome=result,
        effect=active,
        failed=failed,
        critical_failure=critical,
        checks=tuple(checks),
        damage_dice=damage_dice,
    )
    return record(resources, command, event), world, result
