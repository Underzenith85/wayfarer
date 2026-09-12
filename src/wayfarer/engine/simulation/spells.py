"""Versioned Basic Set spell execution on the shared resource ledger.

Execution v2 was reviewed against Characters third printing B235-241,
B246-247 and B249-251. See docs/gurps-spell-execution.md for provenance,
historical pins and the separate frozen-source certification boundary.
"""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.hazard_types import require_hazards_settled
from wayfarer.engine.rules.magic_protocols import (
    AreaSelection,
    CeremonialPlan,
    ceremonial_skill_bonus,
    hex_area,
    item_energy_cost,
    square_area,
)
from wayfarer.engine.rules.recovery_types import interrupt_tasks, require_settled
from wayfarer.engine.simulation.concentration import require_idle_concentration
from wayfarer.engine.simulation.condition_checks import check_modifiers, retching_penalty
from wayfarer.engine.simulation.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
PREFIX = "spell:"
SpellId = Literal["light", "daze", "fireball", "create-fire"]


class SpellSpec(Record):
    id: SpellId
    kind: Literal["regular", "resisted", "missile", "area"]
    cost: int
    maintenance: int
    seconds: int
    duration: int | None
    magery: int = 0
    prerequisites: tuple[str, ...] = ()
    reference: str


SPELLS: dict[str, SpellSpec] = {
    "light": SpellSpec(
        id="light", kind="regular", cost=1, maintenance=1, seconds=1, duration=60, reference="B249"
    ),
    "daze": SpellSpec(
        id="daze",
        kind="resisted",
        cost=3,
        maintenance=2,
        seconds=2,
        duration=60,
        prerequisites=("foolishness",),
        reference="B250",
    ),
    "fireball": SpellSpec(
        id="fireball",
        kind="missile",
        cost=1,
        maintenance=0,
        seconds=1,
        duration=None,
        magery=1,
        prerequisites=("create-fire", "shape-fire"),
        reference="B247",
    ),
    "create-fire": SpellSpec(
        id="create-fire",
        kind="area",
        cost=2,
        maintenance=1,
        seconds=1,
        duration=60,
        prerequisites=("ignite-fire",),
        reference="B246",
    ),
}


class SpellContext(Record):
    """Trusted adapter input; never a player payload or inferred LLM ruling.

    Skill is the compiled trained spell skill, including Magery, before mana,
    range, shock and spells-on penalties. Magery -1 means no Magery advantage.
    """

    profile_id: str
    execution_version: Literal[1, 2] = 1
    iq: int = Field(default=10, ge=1)
    build_revision: Id
    skill: int = Field(ge=1, le=100)
    magery: int = Field(default=0, ge=-1, le=100)
    learned: tuple[str, ...] = ()
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    ht: int = Field(default=10, ge=1)
    will: int = Field(default=10, ge=1)
    target_ht: int = Field(default=10, ge=1)
    distance: int = Field(default=0, ge=0, le=10000)
    target_id: Id
    radius: int = Field(default=1, ge=1, le=100)
    energy: int = Field(default=1, ge=1, le=100)
    distracted: bool = False
    unavailable: bool = False
    execute_effects: bool = False
    location_id: str | None = None
    encounter_id: str | None = None
    position: tuple[int, int] | None = None
    geometry: Literal["square", "hex"] = "square"
    light_radius: int = Field(default=2, ge=0, le=100)
    light_penalty: int = Field(default=-3, ge=-9, le=0)
    ceremonial: CeremonialPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    ceremonial_ht: tuple[tuple[Id, int], ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    item_power_reduction: int = Field(default=0, ge=0, le=100, exclude_if=lambda value: value == 0)
    area: AreaSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class SpellCommand(Command):
    kind: Literal[
        "start",
        "concentrate",
        "complete",
        "maintain",
        "cancel",
        "expand",
        "release",
        "remember",
        "focus",
    ]
    spell_id: SpellId
    cast_id: Id
    target_item_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    channel_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    radius: int = Field(default=1, ge=1, le=100, exclude_if=lambda value: value == 1)
    energy: int = Field(default=1, ge=1, le=100, exclude_if=lambda value: value == 1)
    hp_energy: int = Field(default=0, ge=0, le=1000, exclude_if=lambda value: value == 0)
    position: tuple[int, int] | None = Field(default=None, exclude_if=lambda value: value is None)


class SpellEffect(Record):
    cast_id: Id
    actor_id: Id
    target_id: Id
    spell_id: SpellId
    build_revision: Id
    phase: Literal["casting", "active", "ended"]
    started_at: int
    ready_at: int
    expires_at: int | None = None
    skill: int
    cost: int
    maintenance: int
    hp_at_start: int
    radius: int = 1
    energy: int = 1
    distracted: bool = False
    execute_effects: bool = False
    location_id: str | None = None
    encounter_id: str | None = None
    position: tuple[int, int] | None = None
    geometry: Literal["square", "hex"] = "square"
    light_radius: int = Field(default=2, ge=0, le=100)
    light_penalty: int = Field(default=-3, ge=-9, le=0)
    concentration_seconds: int = 1
    missile_seconds: int = 1
    hp_energy: int = 0
    execution_version: Literal[1, 2] = 1
    required_turns: int | None = None
    concentrating: bool = False
    reversed: bool = False
    ceremonial: CeremonialPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    ceremonial_ht: tuple[tuple[Id, int], ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    area: AreaSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class SpellResult(Record):
    outcome: Literal[
        "casting",
        "active",
        "failed",
        "resisted",
        "cancelled",
        "interrupted",
        "critical-failure",
        "released",
        "remembered",
        "forgotten",
    ]
    energy_spent: int = 0
    hp_spent: int = Field(default=0, exclude_if=lambda value: value == 0)
    checks: tuple[CheckTrace, ...] = ()


class SpellEvent(Record):
    effect: SpellEffect
    result: SpellResult


def event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def latest(state: ResourceState) -> dict[str, SpellEffect]:
    found: dict[str, SpellEffect] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            effect = SpellEvent.model_validate_json(event.kind).effect
            found[effect.cast_id] = effect
    return found


def active_spells(state: ResourceState) -> tuple[SpellEffect, ...]:
    return tuple(
        e
        for e in latest(state).values()
        if e.phase == "active" and (e.expires_at is None or state.game_time < e.expires_at)
    )


def interrupt_spells(
    state: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    events = []
    for effect in latest(state).values():
        if effect.actor_id != actor_id or effect.phase != "casting":
            continue
        effect = effect.model_copy(
            update={"distracted": True} if distraction else {"phase": "ended"}
        )
        record = SpellEvent(
            effect=effect, result=SpellResult(outcome="casting" if distraction else "interrupted")
        )
        events.append(
            ResourceEvent(
                id=event_id(command_id + ":interrupt:" + effect.cast_id),
                at=state.game_time,
                target_id=actor_id,
                kind=record.model_dump_json(),
            )
        )
    return state.model_copy(update={"events": state.events + tuple(events)})


def cost_reduction(skill: int) -> int:
    return 0 if skill < 15 else 1 + (skill - 15) // 5


def casting_seconds(seconds: int, skill: int, *, missile: bool = False) -> int:
    if skill < 10:
        return seconds * 2
    if missile or skill < 20:
        return seconds
    divisor = 1 << (1 + (skill - 20) // 5)
    return max(1, (seconds + divisor - 1) // divisor)


def _spend_ceremonial_energy(
    state: ResourceState,
    effect: SpellEffect,
    command: SpellCommand,
    *,
    rng: RandomSource,
) -> tuple[ResourceState, int, int]:
    """Spend every promised contribution when the ceremonial roll is made."""
    assert effect.ceremonial is not None
    ht = dict(effect.ceremonial_ht)
    total = hp_total = 0
    for contribution in effect.ceremonial.contributions:
        actor_ht = ht.get(contribution.actor_id)
        fp = next((p for p in state.pools if p.id == "fp:" + contribution.actor_id), None)
        if actor_ht is None or fp is None or fp.fatigue is None:
            raise ConflictError("Ceremonial participant binding changed")
        if fp.current < contribution.fp:
            raise ConflictError("Ceremonial participant can no longer supply promised FP")
        if contribution.hp:
            from wayfarer.engine.simulation.injury import Wound, apply_injury

            state, injury = apply_injury(
                state,
                Wound(
                    id=event_id(command.id) + ":ceremony-hp:" + contribution.actor_id,
                    actor_id=contribution.actor_id,
                    expected_revision=state.revision,
                    basic_damage=contribution.hp,
                    resistance=0,
                    damage_type="cr",
                    injury_source="internal",
                ),
                ht=actor_ht,
                rng=rng,
                system=True,
                burning_hp=True,
            )
            if injury.injury != contribution.hp:
                raise ConflictError("Ceremonial participant cannot supply promised HP")
            hp_total += injury.injury
        if contribution.fp:
            state, fatigue = apply_fatigue(
                state,
                FatigueCost(
                    id=event_id(command.id) + ":ceremony-fp:" + contribution.actor_id,
                    actor_id=contribution.actor_id,
                    expected_revision=state.revision,
                    amount=contribution.fp,
                    power=True,
                ),
                ht=actor_ht,
                rng=rng,
                system=True,
            )
            if fatigue.fp_lost != contribution.fp or fatigue.hp_lost:
                raise ConflictError("Ceremonial participant cannot supply promised FP")
        total += contribution.energy
    return state, total, hp_total


def apply_spell(
    state: ResourceState,
    command: SpellCommand,
    context: SpellContext,
    *,
    rng: RandomSource,
    system: bool = False,
    validate_only: bool = False,
) -> tuple[ResourceState, SpellResult]:
    """Commit the returned checkpoint atomically using the existing store CAS.

    Completion must occur at the scheduled time; the turn adapter must call the
    interruption hook for other maneuvers. Late time cannot bank concentration.
    Critical failures persist a terminal result, never an invented table effect.
    """
    if not system or context.profile_id != PROFILE:
        raise ValidationError("Spell execution requires exact Basic Set authority")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Spell command ID reused")
        previous_event = next(e for e in state.events if e.id == event_id(command.id))
        return state, SpellEvent.model_validate_json(previous_event.kind).result
    if state.revision != command.expected_revision:
        raise ConflictError("Spell revision changed")
    require_settled(
        state.recovery_tasks, frozenset({command.actor_id, context.target_id}), state.game_time
    )
    require_hazards_settled(
        state.hazards, frozenset({command.actor_id, context.target_id}), state.game_time
    )
    if command.hp_energy and command.kind in ("concentrate", "release", "focus", "remember"):
        raise ValidationError("This maneuver does not consume spell energy")
    from wayfarer.engine.simulation.spell_backfires import forgotten
    from wayfarer.engine.simulation.spell_backfires import require_settled as backfires_settled

    backfires_settled(state, command.actor_id)
    spec = SPELLS[command.spell_id]
    effect = latest(state).get(command.cast_id)
    fp = next((p for p in state.pools if p.id == "fp:" + command.actor_id), None)
    hp = next((p for p in state.pools if p.id == "hp:" + command.actor_id), None)
    if (
        fp is None
        or hp is None
        or fp.fatigue is None
        or hp.injury is None
        or fp.fatigue.profile_id != PROFILE
        or hp.injury.profile_id != PROFILE
    ):
        raise ValidationError("Spellcasting requires matching GURPS HP and FP pools")
    if effect and (effect.actor_id != command.actor_id or effect.spell_id != command.spell_id):
        raise ConflictError("Cast identity belongs to another actor or spell")
    if command.kind in ("concentrate", "focus") and retching_penalty(state, command.actor_id):
        raise ValidationError("Retching prevents concentration")
    if command.kind != "cancel":
        if context.mana == "none" or (
            context.mana == "very-high" and context.execution_version == 1
        ):
            raise ValidationError("No-mana casting and unaudited very-high mana are unavailable")
        if (
            context.unavailable
            or hp.injury.incapacitated
            or (hp.injury.stunned and command.kind != "maintain")
            or fp.fatigue.collapsed
            or fp.fatigue.unconscious
            or fp.fatigue.heart_attack
        ):
            raise ValidationError("Caster cannot concentrate")
    checks: list[CheckTrace] = []
    spent = 0
    hp_spent = 0
    hp_budget = command.hp_energy
    outcome: Literal[
        "casting",
        "active",
        "failed",
        "resisted",
        "cancelled",
        "interrupted",
        "critical-failure",
        "released",
        "remembered",
        "forgotten",
    ]
    if command.kind == "remember":
        from wayfarer.engine.simulation.spell_backfires import remember

        if effect is None:
            raise ConflictError("Unknown forgotten cast")
        state, memory = remember(
            state, command.actor_id, command.cast_id, command.id, context.iq, rng
        )
        checks.append(memory)
        outcome = "remembered" if memory.outcome.succeeded else "forgotten"
    elif command.kind == "start":
        if forgotten(state, command.actor_id, command.spell_id):
            raise ConflictError("Caster has temporarily forgotten this spell")
        if effect is not None:
            raise ConflictError("Cast identity already used")
        require_idle_concentration(state, command.actor_id)
        if any(
            e.actor_id == command.actor_id and e.spell_id == "fireball"
            for e in active_spells(state)
        ):
            raise ConflictError("Release the held missile before casting another spell")
        if command.spell_id not in context.learned or not set(spec.prerequisites) <= set(
            context.learned
        ):
            raise ValidationError("Spell and prerequisites must be learned")
        if context.magery < spec.magery and not (
            spec.magery == 0 and context.mana in ("high", "very-high")
        ):
            raise ValidationError("Required Magery unavailable")
        if (
            spec.kind != "area"
            and context.radius != 1
            or spec.kind != "missile"
            and context.energy != 1
        ):
            raise ValidationError("Spell does not accept this area or energy")
        if context.area is not None:
            if spec.kind != "area" or context.position != context.area.center:
                raise ValidationError("Area selection must match an Area spell destination")
            (hex_area if context.geometry == "hex" else square_area)(context.area, context.radius)
        if spec.kind == "missile" and context.energy > context.magery:
            raise ValidationError("Initial missile energy exceeds Magery")
        ritual_skill = context.skill - (5 if context.mana == "low" else 0)
        ceremonial = context.ceremonial
        if ceremonial is not None and context.skill < 15:
            raise ValidationError("Ceremonial magic requires leader spell skill 15+")
        reduction = 0 if ceremonial else cost_reduction(ritual_skill)
        scale = (
            context.radius
            if spec.kind == "area"
            else context.energy
            if spec.kind == "missile"
            else 1
        )
        cost = max(
            0,
            item_energy_cost(spec.cost * scale, context.item_power_reduction, context.mana)
            - reduction,
        )
        if command.hp_energy > cost:
            raise ValidationError("HP contribution exceeds the spell energy cost")
        if ceremonial is not None and command.hp_energy:
            raise ValidationError("Ceremonial energy comes from its approved contribution plan")
        if ceremonial is not None and ceremonial.available_energy < cost:
            raise ValidationError("Ceremonial group cannot supply the required spell energy")
        if ceremonial is None and fp.current < cost - command.hp_energy:
            raise ValidationError("Insufficient FP for the selected energy contribution")
        penalty = sum(
            3 if e.concentrating else 1
            for e in active_spells(state)
            if e.actor_id == command.actor_id
        )
        skill = ritual_skill - hp.injury.shock - penalty - command.hp_energy
        if ceremonial is not None:
            # Capping the target at 15 makes 16 an ordinary failure and 17-18
            # critical failures while still recording the energy bonus.
            bonus = ceremonial_skill_bonus(cost, ceremonial.available_energy) if cost else 0
            skill = min(15, skill + bonus)
        if spec.kind != "missile":
            skill -= context.distance
        if skill < 1:
            raise ValidationError("Effective spell skill is below one")
        effect = SpellEffect(
            cast_id=command.cast_id,
            actor_id=command.actor_id,
            target_id=context.target_id,
            spell_id=command.spell_id,
            build_revision=context.build_revision,
            phase="casting",
            started_at=state.game_time,
            ready_at=state.game_time
            + (
                spec.seconds * 10
                if ceremonial is not None
                else casting_seconds(spec.seconds, ritual_skill, missile=spec.kind == "missile")
            )
            - int(context.execution_version == 2 and context.encounter_id is not None),
            skill=skill,
            cost=cost,
            maintenance=max(
                0, spec.maintenance * (context.radius if spec.kind == "area" else 1) - reduction
            ),
            hp_at_start=hp.current,
            radius=context.radius,
            energy=context.energy,
            hp_energy=command.hp_energy,
            execution_version=context.execution_version,
            required_turns=(
                spec.seconds * 10
                if ceremonial is not None
                else casting_seconds(spec.seconds, ritual_skill, missile=spec.kind == "missile")
            )
            if context.execution_version == 2 and context.encounter_id
            else None,
            execute_effects=context.execute_effects,
            location_id=context.location_id,
            encounter_id=context.encounter_id,
            position=context.position,
            geometry=context.geometry,
            light_radius=context.light_radius,
            light_penalty=context.light_penalty,
            ceremonial=ceremonial,
            ceremonial_ht=context.ceremonial_ht,
            area=context.area,
        )
        outcome = "casting"
    else:
        if effect is None or effect.phase == "ended":
            raise ConflictError("Cast is not available")
        if command.kind == "cancel":
            if (
                effect.phase == "active"
                and effect.expires_at is not None
                and state.game_time < effect.expires_at
            ):
                spent = 1  # B237: ending a running spell early is not free.
            effect = effect.model_copy(update={"phase": "ended"})
            outcome = "cancelled"
        else:
            if (
                effect.build_revision != context.build_revision
                or effect.target_id != context.target_id
            ):
                raise ConflictError("Spell binding changed")
            if command.kind == "focus":
                from wayfarer.engine.simulation.combat import CombatEngine, GridPoint
                from wayfarer.engine.simulation.hex_geometry import Hex

                if (
                    effect.phase != "active"
                    or effect.spell_id != "light"
                    or effect.execution_version != 2
                    or effect.position is None
                    or context.position is None
                    or effect.geometry != context.geometry
                ):
                    raise ValidationError("Only an active Light supports this manipulation")
                origin = (
                    Hex(q=effect.position[0], r=effect.position[1])
                    if effect.geometry == "hex"
                    else GridPoint(x=effect.position[0], y=effect.position[1])
                )
                destination = (
                    Hex(q=context.position[0], r=context.position[1])
                    if effect.geometry == "hex"
                    else GridPoint(x=context.position[0], y=context.position[1])
                )
                if CombatEngine.distance(origin, destination) > 5:
                    raise ValidationError("Light manipulation exceeds Move 5")
                effect = effect.model_copy(
                    update={"position": context.position, "concentrating": True}
                )
                outcome = "active"
            elif command.kind == "release":
                if (
                    spec.kind != "missile"
                    or effect.phase != "active"
                    or state.game_time < effect.ready_at
                ):
                    raise ConflictError("Release requires a held missile")
                outcome = "released"
            elif command.kind == "expand":
                if (
                    spec.kind != "missile"
                    or effect.phase != "active"
                    or effect.missile_seconds >= 3
                    or context.energy > context.magery
                    or state.game_time
                    != effect.ready_at
                    + int(effect.execution_version == 2 and effect.encounter_id is not None)
                ):
                    raise ConflictError(
                        "Enlarge a held missile on the next casting second, at most three seconds"
                    )
                new_energy = effect.energy + context.energy
                new_cost = max(
                    0,
                    new_energy
                    - cost_reduction(context.skill - (5 if context.mana == "low" else 0)),
                )
                spent = new_cost - effect.cost
                effect = effect.model_copy(
                    update={
                        "energy": new_energy,
                        "cost": new_cost,
                        "missile_seconds": effect.missile_seconds + 1,
                        "ready_at": state.game_time
                        + int(effect.execution_version == 1 or effect.encounter_id is None),
                    }
                )
                outcome = "active"
            elif command.kind == "concentrate":
                if (
                    effect.phase != "casting"
                    or state.game_time != effect.started_at + effect.concentration_seconds
                    or (
                        state.game_time > effect.ready_at
                        if effect.required_turns
                        else state.game_time >= effect.ready_at
                    )
                ):
                    raise ConflictError("Concentrate on each consecutive casting second")
                effect = effect.model_copy(
                    update={"concentration_seconds": effect.concentration_seconds + 1}
                )
                outcome = "casting"
            elif command.kind == "maintain":
                if (
                    effect.phase != "active"
                    or effect.expires_at != state.game_time
                    or spec.duration is None
                    or spec.kind == "missile"
                ):
                    raise ConflictError("Maintenance is only available at expiry")
                spent = effect.maintenance
                effect = effect.model_copy(update={"expires_at": state.game_time + spec.duration})
                outcome = "active"
            else:
                if effect.phase != "casting" or state.game_time != effect.ready_at:
                    raise ConflictError("Complete concentration at its shared-clock deadline")
                if effect.execute_effects and effect.concentration_seconds != (
                    effect.required_turns or effect.ready_at - effect.started_at
                ):
                    raise ConflictError("Every casting second requires concentration")
                hp_budget = effect.hp_energy
                if fp.current < effect.cost - hp_budget:
                    raise ConflictError("Caster no longer has casting energy")
                interrupted = bool(retching_penalty(state, command.actor_id))
                if not interrupted and (
                    effect.distracted or context.distracted or hp.current < effect.hp_at_start
                ):
                    check = success_roll(
                        PROFILE,
                        context.will - 3,
                        check_modifiers(state, command.actor_id, "will"),
                        rng=rng,
                    )
                    checks.append(check)
                    interrupted = not check.outcome.succeeded
                if interrupted:
                    outcome = "interrupted"
                else:
                    check = success_roll(
                        PROFILE,
                        effect.skill,
                        check_modifiers(state, command.actor_id, "iq"),
                        rng=rng,
                    )
                    checks.append(check)
                    if check.outcome is Outcome.CRITICAL_FAILURE or (
                        context.mana == "very-high" and not check.outcome.succeeded
                    ):
                        outcome, spent = "critical-failure", effect.cost
                    elif not check.outcome.succeeded:
                        outcome, spent = "failed", min(1, effect.cost)
                    else:
                        outcome = "active"
                        spent = 0 if check.outcome is Outcome.CRITICAL_SUCCESS else effect.cost
                        if (
                            spec.kind == "resisted"
                            and check.outcome is not Outcome.CRITICAL_SUCCESS
                        ):
                            resistance = success_roll(
                                PROFILE,
                                context.target_ht,
                                check_modifiers(state, effect.target_id, "ht"),
                                rng=rng,
                            )
                            checks.append(resistance)
                            # One casting roll serves as the attack roll. Rule of 16
                            # caps its contest margin; failed casting never affects.
                            margin = min(
                                check.effective_target, max(16, resistance.effective_target)
                            ) - sum(check.dice)
                            if resistance.outcome.succeeded and resistance.margin >= margin:
                                outcome = "resisted"
                effect = effect.model_copy(
                    update={
                        "phase": "active" if outcome == "active" else "ended",
                        "expires_at": state.game_time + spec.duration
                        if outcome == "active" and spec.duration
                        else None,
                    }
                )
    assert effect is not None
    if command.kind in ("maintain", "cancel", "expand") and hp_budget > spent:
        raise ValidationError("HP contribution exceeds this operation's energy cost")
    ceremonial_payment = command.kind == "complete" and effect.ceremonial is not None
    if ceremonial_payment:
        state, spent, hp_spent = _spend_ceremonial_energy(state, effect, command, rng=rng)
        hp_cost = fp_cost = 0
    else:
        hp_cost = min(hp_budget, spent)
        fp_cost = spent - hp_cost
        if fp.current < fp_cost:
            raise ConflictError("Caster no longer has reserved casting energy")
    if validate_only:
        if command.kind not in ("release", "expand"):
            raise ValidationError("Preflight supports only missile maneuvers")
        return state, SpellResult(outcome=outcome, energy_spent=spent, hp_spent=hp_cost)
    if hp_cost:
        from wayfarer.engine.simulation.injury import Wound, apply_injury

        state, injury = apply_injury(
            state,
            Wound(
                id=event_id(command.id) + ":hp",
                actor_id=command.actor_id,
                expected_revision=state.revision,
                basic_damage=hp_cost,
                resistance=0,
                damage_type="cr",
            ),
            ht=context.ht,
            rng=rng,
            system=True,
            burning_hp=True,
        )
        hp_spent = injury.injury
        if hp_spent != hp_cost:
            outcome = "interrupted"
            effect = effect.model_copy(update={"phase": "ended"})
            fp_cost = 0
        spent = hp_spent + fp_cost
    if fp_cost:
        if fp.current < fp_cost:
            raise ConflictError("Caster no longer has reserved casting energy")
        state, _ = apply_fatigue(
            state,
            FatigueCost(
                id=event_id(command.id) + ":energy",
                actor_id=command.actor_id,
                expected_revision=state.revision,
                amount=fp_cost,
                power=True,
            ),
            ht=context.ht,
            rng=rng,
            system=True,
        )
    if (
        fp_cost
        and context.mana == "very-high"
        and context.magery >= 0
        and command.kind in ("complete", "expand")
    ):
        from wayfarer.engine.simulation.spell_backfires import refund_later

        state = refund_later(
            state,
            command.actor_id,
            command.id,
            fp_cost,
            combat=context.encounter_id is not None,
            before_turn=False,
        )
    if outcome == "critical-failure" and context.execution_version == 2:
        from wayfarer.engine.simulation.spell_backfires import apply_backfire

        actual_critical = bool(checks and checks[-1].outcome is Outcome.CRITICAL_FAILURE)
        state = apply_backfire(
            state,
            command_id=command.id,
            actor_id=command.actor_id,
            cast_id=command.cast_id,
            spell_id=command.spell_id,
            ht=context.ht,
            severity="mild"
            if context.mana == "low"
            else "disaster"
            if context.mana == "very-high" and actual_critical
            else "normal",
            rng=rng,
        )
    if outcome == "resisted":
        from wayfarer.engine.simulation.spell_effects import break_daze

        state = break_daze(state, context.target_id, command.id)
    state = state.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                state.recovery_tasks, frozenset({command.actor_id}), state.game_time
            )
        }
    )
    result = SpellResult(
        outcome=outcome, energy_spent=spent, hp_spent=hp_spent, checks=tuple(checks)
    )
    event = SpellEvent(effect=effect, result=result)
    return state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id(command.id),
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    ), result
