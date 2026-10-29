"""Executable supernatural effects on the existing resource/knowledge ledgers."""

import hashlib
from dataclasses import dataclass, replace

from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    resistance_roll,
    success_roll,
)
from wayfarer.engine.rules.resistance_attempts import (
    ResistanceAttempt,
    ResistancePreparation,
    prepare_resistance_attempt,
    resistance_attempt,
    resistance_attempt_checks,
)
from wayfarer.engine.rules.supernatural.abilities import PROFILE, fatigue_cost, validate_binding
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.simulation.ability_state import PREFIX
from wayfarer.engine.simulation.ability_state import effects as effects
from wayfarer.engine.simulation.ability_state import history as history
from wayfarer.engine.simulation.ability_types import (
    AbilityChannel,
    AbilityCommand,
    AbilityEffect,
    AbilityEvent,
    AbilityOutcome,
    AbilityOutcomeKind,
    AbilitySpec,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers, retching_penalty
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.limb_spell_state import (
    interrupt_casts as interrupt_limb_casts,
)
from wayfarer.engine.simulation.magic.melee_spell_state import (
    interrupt_casts as interrupt_melee_casts,
)
from wayfarer.engine.simulation.magic.spell_state import interrupt_spells
from wayfarer.engine.simulation.magic.wither_spell_state import (
    interrupt_casts as interrupt_wither_casts,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.traits.neutralization import power_suppressed
from wayfarer.engine.simulation.traits.psi_protection import protects
from wayfarer.engine.simulation.traits.size_forms import reduced_body_result
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError


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


def damage_resistance(
    resources: ResourceState, actor_id: str, *, build_revision: str | None = None
) -> int:
    """Combat adapters consume the same expiring defense, never a second DR state."""
    resistance = sum(
        e.level
        for e in effects(resources)
        if e.actor_id == actor_id
        and e.kind == "damage-resistance"
        and not e.concentrating
        and (build_revision is None or e.build_revision == build_revision)
    )
    return reduced_body_result(resources, actor_id, resistance)


def interrupt_concentration(
    resources: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    """Other maneuvers abandon concentration; an active defense needs Will-3."""
    resources = interrupt_spells(resources, actor_id, command_id, distraction=distraction)
    resources = interrupt_melee_casts(resources, actor_id, command_id, distraction=distraction)
    resources = interrupt_limb_casts(resources, actor_id, command_id, distraction=distraction)
    resources = interrupt_wither_casts(resources, actor_id, command_id, distraction=distraction)
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
    power_id: str | None = None
    target_psi_static: bool | None = None
    target_living_or_sapient: bool = True
    current_resistance: bool = False
    resistance_preparation: ResistancePreparation | None = None


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


def _require_available_power(
    resources: ResourceState, command: AbilityCommand, spec: AbilitySpec, context: AbilityContext
) -> None:
    selected_power = context.power_id or (
        "power:telepathy" if "telepathic" in spec.modifiers else None
    )
    if (
        selected_power is not None
        and command.kind != "cancel"
        and (
            power_suppressed(resources, command.actor_id, selected_power)
            or (
                context.channel is not None
                and protects(
                    resources, context.channel.target_id, purchased=context.target_psi_static
                )
            )
        )
    ):
        raise ValidationError("Psionic power is suppressed by active Antipsi")


def mind_reading_attacker(
    resources: ResourceState,
    command: AbilityCommand,
    context: AbilityContext,
    old: AbilityEffect | None,
) -> Contestant:
    """The native B69 target, shared by admission and the actual contest."""
    channel = context.channel
    assert channel is not None
    attempts = [
        (at, e)
        for at, e in history(resources)
        if e.actor_id == command.actor_id
        and e.ability_id == command.ability_id
        and e.target_id == channel.target_id
        and e.failed
    ]
    if any(e.critical_failure and at + 86400 > resources.game_time for at, e in attempts):
        raise ConflictError("Mind Reading retry blocked for 24 hours")
    penalty = -2 * sum(at + 3600 > resources.game_time for at, _ in attempts)
    return Contestant(
        command.actor_id,
        context.iq
        - (old.activation_shock if old else context.shock)
        + range_penalty(channel.distance_yards)
        + penalty,
        check_modifiers(resources, command.actor_id, "iq"),
    )


def prepare_ability_resistance(
    resources: ResourceState,
    command: AbilityCommand,
    spec: AbilitySpec,
    context: AbilityContext,
) -> ResistancePreparation | None:
    """The same canonical voluntary score is admitted before health/FP entropy."""
    if (
        not context.current_resistance
        or command.kind not in {"activate", "resolve"}
        or spec.kind not in {"burning-malediction", "mind-reading"}
    ):
        return None
    channel = context.channel
    assert channel is not None
    old = next(
        (
            e
            for e in effects(resources)
            if e.actor_id == command.actor_id and e.ability_id == command.ability_id
        ),
        None,
    )
    attacker = (
        mind_reading_attacker(
            resources, command, context, old if command.kind == "resolve" else None
        )
        if spec.kind == "mind-reading"
        else Contestant(
            command.actor_id,
            context.will - channel.distance_yards,
            check_modifiers(resources, command.actor_id, "will"),
        )
    )
    return prepare_resistance_attempt(
        PROFILE,
        attacker,
        Contestant(
            channel.target_id,
            context.target_will
            + (
                context.mind_shield
                if spec.kind == "mind-reading" and "telepathic" in spec.modifiers
                else 0
            ),
            check_modifiers(resources, channel.target_id, "will", defensive=True),
        ),
        rule_of_16=context.target_living_or_sapient,
    )


def admit_ability_resistance(
    resources: ResourceState, command: AbilityCommand, spec: AbilitySpec, context: AbilityContext
) -> AbilityContext:
    prepared = context.resistance_preparation or prepare_ability_resistance(
        resources, command, spec, context
    )
    return replace(context, resistance_preparation=prepared)


def _captured_preparation(
    context: AbilityContext, trace: ResistanceAttempt | None
) -> ResistancePreparation | None:
    return context.resistance_preparation if trace is not None else None


def _ability_resistance(
    resources: ResourceState,
    command: AbilityCommand,
    spec: AbilitySpec,
    context: AbilityContext,
    old: AbilityEffect | None,
    rng: RandomSource,
) -> tuple[ResistanceAttempt, ResistanceAttempt | None]:
    if context.current_resistance:
        prepared = context.resistance_preparation or prepare_ability_resistance(
            resources, command, spec, context
        )
        assert prepared is not None
        trace = resistance_attempt(prepared, rng=rng)
        return trace, trace
    channel = context.channel
    assert channel is not None
    attacker = (
        mind_reading_attacker(resources, command, context, old)
        if spec.kind == "mind-reading"
        else Contestant(
            command.actor_id,
            context.will - channel.distance_yards,
            check_modifiers(resources, command.actor_id, "will"),
        )
    )
    historical = resistance_roll(
        PROFILE,
        attacker,
        Contestant(
            channel.target_id,
            context.target_will
            + (
                context.mind_shield
                if spec.kind == "mind-reading" and "telepathic" in spec.modifiers
                else 0
            ),
            check_modifiers(resources, channel.target_id, "will", defensive=True),
        ),
        rng=rng,
        rule_of_16=context.target_living_or_sapient,
    )
    return historical, None


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
        require_idle_concentration(resources, command.actor_id)
    _require_available_power(resources, command, spec, context)
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
    context = admit_ability_resistance(resources, command, spec, context)
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
    captured_attempt: ResistanceAttempt | None = None
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
        trace, captured_attempt = _ability_resistance(resources, command, spec, context, old, rng)
        failed, critical = not trace.affected, trace.attacker.outcome is Outcome.CRITICAL_FAILURE
        checks.extend(resistance_attempt_checks(trace))
        outcome = "resisted" if failed else "hit"
        if not failed:
            damage_dice = tuple(rng.randbelow(6) + 1 for _ in range(context.level))
            damage = reduced_body_result(resources, command.actor_id, sum(damage_dice))
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
        trace, captured_attempt = _ability_resistance(resources, command, spec, context, old, rng)
        failed, critical = not trace.affected, trace.attacker.outcome is Outcome.CRITICAL_FAILURE
        checks.extend(resistance_attempt_checks(trace))
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
        resistance_attempt=captured_attempt,
        resistance_preparation=_captured_preparation(context, captured_attempt),
    )
    return record(resources, command, event), world, result
