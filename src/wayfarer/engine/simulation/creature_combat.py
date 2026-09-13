"""Animal natural attacks, constrained proposals, and swarm turns.

Selected source: Campaigns fourth printing B460-B461.  Every state transition
uses the resource CAS/receipt ledger and delegates bodily harm to ``apply_injury``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field

from wayfarer.engine.character.statistics import damage as strength_damage
from wayfarer.engine.rules.checks import CheckTrace, RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.creature import (
    Creature,
    CreatureAttack,
    CreatureManeuver,
    CreatureMotivation,
    Swarm,
    SwarmCell,
    SwarmCountermeasure,
    SwarmOccupant,
)
from wayfarer.engine.rules.types.location import InjuryTolerance
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.injury import InjuryResult, Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Hex, distance
from wayfarer.engine.simulation.resources import (
    Command,
    Pool,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE = "gurps-basic-set-4e-2004"
EVENT_PREFIX = "creature-combat:"


class CreatureActionProposal(Record):
    creature_id: Id
    maneuver: CreatureManeuver
    attack_id: Id | None = None
    defenses: tuple[Literal["dodge", "parry"], ...] = ()
    motivation: CreatureMotivation


class ResolveNaturalAttack(Command):
    kind: Literal["creature-natural-attack"] = "creature-natural-attack"
    target_id: Id
    attack_id: Id
    maneuver: Literal["attack", "all-out-attack", "move-and-attack"] = "attack"
    motivation: CreatureMotivation
    attacker_position: SwarmCell
    target_position: SwarmCell
    defense: Literal["none", "dodge", "parry"] = "dodge"
    defense_score: int | None = Field(default=None, ge=1, le=100)
    target_ht: int = Field(ge=1, le=100)
    target_dr: int = Field(default=0, ge=0, le=1000)
    mounted_transport_id: Id | None = None


class NaturalAttackOutcome(Record):
    attacker_id: Id
    target_id: Id
    attack_id: Id
    hit: bool
    attack: CheckTrace
    defense: CheckTrace | None = None
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = 0
    injury: InjuryResult | None = None


class SetSwarmArea(Command):
    kind: Literal["swarm-set-area"] = "swarm-set-area"
    swarm_id: Id
    area: tuple[SwarmCell, ...] = Field(min_length=1, max_length=100)
    occupants: tuple[SwarmOccupant, ...] = ()


class ResolveSwarmTurn(Command):
    kind: Literal["swarm-resolve-turn"] = "swarm-resolve-turn"
    swarm_id: Id


class DamageSwarm(Command):
    kind: Literal["swarm-damage"] = "swarm-damage"
    swarm_id: Id
    countermeasure: SwarmCountermeasure
    basic_damage: int = Field(default=0, ge=0, le=10000)
    damage_type: Literal["cr", "cut", "imp", "tox", "burn", "cor"] = "cr"


CreatureCombatCommand = Annotated[
    ResolveNaturalAttack | SetSwarmArea | ResolveSwarmTurn | DamageSwarm,
    Field(discriminator="kind"),
]


class SwarmTargetOutcome(Record):
    actor_id: Id
    protected: bool
    damage_dice: tuple[int, ...] = ()
    injury: InjuryResult | None = None


class SwarmTurnOutcome(Record):
    swarm_id: Id
    targets: tuple[SwarmTargetOutcome, ...]
    next_attack_at: int


class SwarmDamageOutcome(Record):
    swarm_id: Id
    countermeasure: SwarmCountermeasure
    ignored: bool
    injury: InjuryResult | None = None
    remaining_hp: int
    dispersed: bool


class CreatureCombatEvent(Record):
    command_id: Id
    command_digest: str
    outcome: NaturalAttackOutcome | SwarmTurnOutcome | SwarmDamageOutcome | None = None


CreatureCombatOutcome = NaturalAttackOutcome | SwarmTurnOutcome | SwarmDamageOutcome | None


def _digest(command: Command) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _event_id(command_id: str) -> str:
    return EVENT_PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _previous(state: ResourceState, command: Command) -> CreatureCombatOutcome | Literal[False]:
    event = next((entry for entry in state.events if entry.id == _event_id(command.id)), None)
    if event is None:
        return False
    value = CreatureCombatEvent.model_validate_json(event.kind)
    if value.command_digest != _digest(command):
        raise ConflictError("Creature combat command ID reused with a different payload")
    return value.outcome


def _creature(state: ResourceState, actor_id: str) -> Creature:
    value = next((entry for entry in state.creatures if entry.actor_id == actor_id), None)
    if value is None:
        raise ValidationError("Unknown compiled creature")
    return value


def _hp(state: ResourceState, actor_id: str) -> Pool:
    pool = next((entry for entry in state.pools if entry.id == "hp:" + actor_id), None)
    if pool is None or pool.injury is None or pool.injury.profile_id != PROFILE:
        raise ValidationError("Creature combat requires a canonical Basic Set injury pool")
    return pool


def _commanded_attack(creature: Creature, *, mounted: bool) -> bool:
    if mounted and creature.mount is not None and creature.mount.war_trained:
        return True
    selected = next(
        (entry for entry in creature.commands if entry.id == creature.last_command_id), None
    )
    return selected is not None and selected.task in ("guard", "hunt")


def _defenses(creature: Creature) -> tuple[Literal["dodge", "parry"], ...]:
    no_manipulators = any(
        trait.id in {"quadruped", "vermiform", "ichthyoid", "no-fine-manipulators"}
        for trait in creature.traits
    )
    return ("dodge",) if no_manipulators else ("dodge", "parry")


def propose_creature_actions(
    state: ResourceState,
    creature_id: str,
    motivation: CreatureMotivation,
    *,
    mounted_transport_id: str | None = None,
) -> tuple[CreatureActionProposal, ...]:
    """Return only actions allowed by authored behavior, training, anatomy and condition."""
    creature = _creature(state, creature_id)
    status = _hp(state, creature_id).injury
    assert status is not None
    if status.dead or status.incapacitated or status.stunned:
        return (
            CreatureActionProposal(
                creature_id=creature_id,
                maneuver="do-nothing",
                motivation=motivation,
            ),
        )
    mounted = mounted_transport_id is not None
    if mounted_transport_id is not None:
        _mounted_transport(state, creature, mounted_transport_id)
    maneuvers = list(creature.combat_behavior.maneuvers)
    if status.prone:
        maneuvers = [value for value in maneuvers if value != "move-and-attack"]
    may_attack = motivation in creature.combat_behavior.attack_motivations
    if motivation == "commanded":
        may_attack = may_attack and _commanded_attack(creature, mounted=mounted)
    if mounted and (creature.mount is None or not creature.mount.war_trained):
        may_attack = False
    proposals: list[CreatureActionProposal] = []
    for maneuver in maneuvers:
        if maneuver in ("attack", "all-out-attack", "move-and-attack"):
            if not may_attack:
                continue
            for attack_id in creature.combat_behavior.preferred_attack_ids:
                proposals.append(
                    CreatureActionProposal(
                        creature_id=creature_id,
                        maneuver=maneuver,
                        attack_id=attack_id,
                        defenses=() if maneuver == "all-out-attack" else _defenses(creature),
                        motivation=motivation,
                    )
                )
        else:
            proposals.append(
                CreatureActionProposal(
                    creature_id=creature_id,
                    maneuver=maneuver,
                    defenses=_defenses(creature),
                    motivation=motivation,
                )
            )
    return tuple(proposals)


def _mounted_transport(state: ResourceState, creature: Creature, transport_id: str) -> Transport:
    transport = next((entry for entry in state.transports if entry.id == transport_id), None)
    if (
        transport is None
        or transport.locomotion != "ground-mount"
        or transport.body_id != creature.actor_id
        or transport.status in {"rider-separated", "mount-fallen", "crashed", "lost"}
        or transport.operator_id not in transport.occupants
    ):
        raise ValidationError("Mounted creature attack requires the active #396/#397 transport")
    return transport


def _attack_level(creature: Creature) -> int:
    brawling = next(
        (entry.level for entry in creature.skills if entry.id == "skill:brawling"), None
    )
    return creature.statistics.dx if brawling is None else brawling


def _require_other_target(command: ResolveNaturalAttack, creature: Creature) -> None:
    if command.target_id == creature.actor_id:
        raise ValidationError("Creature natural attacks require a different target")


def _natural_damage(
    creature: Creature, attack_id: str, rng: RandomSource
) -> tuple[CreatureAttack, tuple[int, ...], int]:
    attack = next((entry for entry in creature.attacks if entry.id == attack_id), None)
    if attack is None:
        raise ValidationError("Natural attack is not in the compiled creature")
    if attack.damage_basis == "special" or attack.damage_type == "special":
        raise ValidationError(
            "Special monster attacks require their own implemented trait procedure"
        )
    expression = strength_damage(PROFILE, creature.statistics.st)[0]
    adds = expression.add - (1 if attack.damage_basis == "thrust-1" else 0)
    traits = {entry.id for entry in creature.traits}
    if attack.form == "bite" and "weak-bite" in traits:
        adds -= 2 * expression.dice
    if attack.form == "claw" and "blunt-claws" in traits:
        adds += expression.dice
    if attack.form == "kick":
        if traits & {"blunt-claws", "hooves"}:
            adds += expression.dice
        if "quadruped" in traits and not traits & {"blunt-claws", "sharp-claws"}:
            adds -= expression.dice
    if _attack_level(creature) >= creature.statistics.dx + 2:
        adds += expression.dice
    dice = draw_dice(rng, expression.dice)
    minimum = 0 if attack.damage_type == "crushing" else 1
    return attack, dice, max(minimum, sum(dice) + adds)


def _append_event(
    state: ResourceState, command: Command, outcome: CreatureCombatOutcome, target_id: str
) -> ResourceState:
    event = CreatureCombatEvent(
        command_id=command.id, command_digest=_digest(command), outcome=outcome
    )
    return state.model_copy(
        update={
            "revision": (
                command.expected_revision + 1
                if state.revision == command.expected_revision
                else state.revision
            ),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": state.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=state.game_time,
                    kind=event.model_dump_json(),
                    target_id=target_id,
                ),
            ),
        }
    )


def resolve_natural_attack(
    state: ResourceState,
    command: ResolveNaturalAttack,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, NaturalAttackOutcome]:
    if not system:
        raise ValidationError("Natural attack resolution requires engine authority")
    previous = _previous(state, command)
    if previous is not False:
        if not isinstance(previous, NaturalAttackOutcome):
            raise ConflictError("Creature combat command kind changed")
        return state, previous
    if command.expected_revision != state.revision:
        raise ConflictError("Creature combat resource revision changed")
    creature = _creature(state, command.actor_id)
    _require_other_target(command, creature)
    proposal = CreatureActionProposal(
        creature_id=creature.actor_id,
        maneuver=command.maneuver,
        attack_id=command.attack_id,
        defenses=() if command.maneuver == "all-out-attack" else _defenses(creature),
        motivation=command.motivation,
    )
    legal = propose_creature_actions(
        state,
        creature.actor_id,
        command.motivation,
        mounted_transport_id=command.mounted_transport_id,
    )
    if proposal not in legal:
        raise ValidationError(
            "Creature attack is excluded by behavior, training, anatomy or condition"
        )
    selected = next((entry for entry in creature.attacks if entry.id == command.attack_id), None)
    if selected is None:
        raise ValidationError("Unknown natural attack")
    separation = distance(
        Hex(q=command.attacker_position.q, r=command.attacker_position.r),
        Hex(q=command.target_position.q, r=command.target_position.r),
    )
    if separation > selected.reach:
        raise ValidationError("Target is outside the compiled natural-attack reach")
    if selected.damage_basis == "special" or selected.damage_type == "special":
        raise ValidationError(
            "Special monster attacks require their own implemented trait procedure"
        )
    defender = next(
        (entry for entry in state.creatures if entry.actor_id == command.target_id), None
    )
    if defender is not None and (
        command.target_ht != defender.statistics.ht or command.target_dr < defender.statistics.dr
    ):
        raise ValidationError("Target facts disagree with the compiled creature")
    attack_target = _attack_level(creature)
    if command.maneuver == "all-out-attack":
        attack_target += 4
    elif command.maneuver == "move-and-attack":
        attack_target = min(9, attack_target - 4)
    attack_trace = success_roll(PROFILE, attack_target, rng=rng)
    hit = attack_trace.outcome.succeeded
    defense_trace = None
    if hit and command.defense != "none":
        defense_score: int | None
        if defender is not None:
            allowed = _defenses(defender)
            defense_score = (
                defender.statistics.dodge
                if command.defense == "dodge"
                else max(
                    defender.statistics.dx // 2 + 3,
                    _attack_level(defender) // 2 + 3,
                )
            )
        else:
            allowed = ("dodge", "parry")
            defense_score = command.defense_score
        if command.defense not in allowed or defense_score is None:
            raise ValidationError("Selected active defense is unavailable")
        defense_trace = success_roll(PROFILE, defense_score, rng=rng)
        hit = not defense_trace.outcome.succeeded
    damage_dice: tuple[int, ...] = ()
    basic_damage = 0
    injury = None
    updated = state
    if hit:
        selected, damage_dice, basic_damage = _natural_damage(creature, command.attack_id, rng)
        damage_types: Mapping[str, DamageType] = {
            "crushing": "cr",
            "cutting": "cut",
            "impaling": "imp",
            "toxic": "tox",
        }
        updated, injury = apply_injury(
            state,
            Wound(
                id=EVENT_PREFIX + "injury:" + hashlib.sha256(command.id.encode()).hexdigest(),
                actor_id=command.target_id,
                expected_revision=state.revision,
                basic_damage=basic_damage,
                resistance=command.target_dr,
                damage_type=damage_types[selected.damage_type],
            ),
            ht=command.target_ht,
            rng=rng,
            system=True,
        )
    outcome = NaturalAttackOutcome(
        attacker_id=creature.actor_id,
        target_id=command.target_id,
        attack_id=command.attack_id,
        hit=hit,
        attack=attack_trace,
        defense=defense_trace,
        damage_dice=damage_dice,
        basic_damage=basic_damage,
        injury=injury,
    )
    return _append_event(updated, command, outcome, command.target_id), outcome


def _swarm(state: ResourceState, swarm_id: str) -> Swarm:
    value = next((entry for entry in state.swarms if entry.id == swarm_id), None)
    if value is None:
        raise ValidationError("Unknown swarm")
    return value


def _replace_swarm(state: ResourceState, swarm: Swarm) -> ResourceState:
    found = any(entry.id == swarm.id for entry in state.swarms)
    values = [swarm if entry.id == swarm.id else entry for entry in state.swarms]
    if not found:
        values.append(swarm)
    return state.model_copy(update={"swarms": tuple(sorted(values, key=lambda entry: entry.id))})


def _protected(swarm: Swarm, occupant: SwarmOccupant, now: int) -> bool:
    if occupant.protection == "sealed":
        return True
    elapsed = now - occupant.entered_at
    if occupant.protection == "ordinary-clothing":
        return elapsed < swarm.spec.ordinary_clothing_seconds
    if occupant.protection == "low-tech-armor":
        return elapsed < swarm.spec.low_tech_armor_seconds
    return False


def _set_swarm_area(state: ResourceState, command: SetSwarmArea) -> ResourceState:
    swarm = _swarm(state, command.swarm_id)
    if not swarm.active:
        raise ConflictError("Dispersed swarm cannot change area")
    # Shared geometry owns adjacency: every destination cell must connect to the area.
    cells = tuple(Hex(q=entry.q, r=entry.r) for entry in command.area)
    if len(cells) > 1 and any(
        not any(distance(cell, other) == 1 for other in cells if other != cell) for cell in cells
    ):
        raise ValidationError("Swarm area must be connected in shared hex geometry")
    old = tuple(Hex(q=entry.q, r=entry.r) for entry in swarm.area)
    if any(min(distance(cell, origin) for origin in old) > swarm.spec.move for cell in cells):
        raise ValidationError("Swarm area movement exceeds its compiled Move")
    if any(entry.entered_at > state.game_time for entry in command.occupants):
        raise ValidationError("Swarm occupancy cannot begin in the future")
    moved = Swarm.model_validate(
        {**swarm.model_dump(), "area": command.area, "occupants": command.occupants}
    )
    return _replace_swarm(state.model_copy(update={"revision": state.revision + 1}), moved)


def _resolve_swarm_turn(
    state: ResourceState, command: ResolveSwarmTurn, rng: RandomSource
) -> tuple[ResourceState, SwarmTurnOutcome]:
    swarm = _swarm(state, command.swarm_id)
    if not swarm.active:
        raise ConflictError("Dispersed swarm cannot attack")
    if state.game_time < swarm.next_attack_at:
        raise ConflictError("Swarm attack cadence has not elapsed")
    updated = state
    outcomes: list[SwarmTargetOutcome] = []
    for occupant in swarm.occupants:
        if occupant.position not in swarm.area:
            raise ValidationError("Swarm target is outside its authoritative area")
        if occupant.entered_at > state.game_time:
            raise ValidationError("Swarm occupancy cannot begin in the future")
        protected = _protected(swarm, occupant, state.game_time)
        if protected:
            outcomes.append(SwarmTargetOutcome(actor_id=occupant.actor_id, protected=True))
            continue
        attack = swarm.spec.attack
        dice = draw_dice(rng, attack.dice) if attack.dice else ()
        basic = attack.fixed_injury or max(0, sum(dice) + attack.adds)
        resistance = occupant.armor_dr if attack.armor == "normal-dr" else 0
        updated, result = apply_injury(
            updated,
            Wound(
                id=f"{EVENT_PREFIX}swarm:{hashlib.sha256(command.id.encode()).hexdigest()}:{occupant.actor_id}",
                actor_id=occupant.actor_id,
                expected_revision=updated.revision,
                basic_damage=basic,
                resistance=resistance,
                damage_type=attack.damage_type,
                injury_source="area",
            ),
            ht=occupant.ht,
            rng=rng,
            system=True,
        )
        outcomes.append(
            SwarmTargetOutcome(
                actor_id=occupant.actor_id, protected=False, damage_dice=dice, injury=result
            )
        )
    next_at = state.game_time + swarm.spec.attack.cadence_seconds
    updated = _replace_swarm(updated, swarm.model_copy(update={"next_attack_at": next_at}))
    return updated, SwarmTurnOutcome(
        swarm_id=swarm.id, targets=tuple(outcomes), next_attack_at=next_at
    )


def _damage_swarm(
    state: ResourceState, command: DamageSwarm, rng: RandomSource
) -> tuple[ResourceState, SwarmDamageOutcome]:
    swarm = _swarm(state, command.swarm_id)
    if not swarm.active:
        raise ConflictError("Swarm already dispersed")
    if command.countermeasure in swarm.spec.immune_countermeasures:
        return state.model_copy(update={"revision": state.revision + 1}), SwarmDamageOutcome(
            swarm_id=swarm.id,
            countermeasure=command.countermeasure,
            ignored=True,
            remaining_hp=swarm.remaining_hp,
            dispersed=False,
        )
    basic = command.basic_damage
    if command.countermeasure == "shield":
        basic = 2
    elif command.countermeasure == "stomp":
        if swarm.spec.airborne:
            raise ValidationError("Stomping cannot affect an airborne swarm")
        basic = 1
    elif command.countermeasure in ("insecticide", "immersion") and (
        command.countermeasure not in swarm.spec.vulnerable_countermeasures
    ):
        raise ValidationError("Special countermeasure is not authored for this swarm")
    if basic < 1:
        raise ValidationError("Swarm damage requires positive resolved damage")
    pool = _hp(state, swarm.actor_id)
    status = pool.injury
    assert status is not None
    if status.tolerance is None:
        status = status.model_copy(
            update={
                "anatomy": "swarm",
                "tolerance": InjuryTolerance(
                    structure="diffuse", no_brain=True, no_eyes=True, no_neck=True, no_vitals=True
                ),
            }
        )
        state = state.model_copy(
            update={
                "pools": tuple(
                    entry.model_copy(update={"injury": status}) if entry.id == pool.id else entry
                    for entry in state.pools
                )
            }
        )
    elif status.tolerance.structure != "diffuse":
        raise ValidationError("Swarm injury pool requires diffuse tolerance")
    area = command.countermeasure in {"area-attack", "insecticide", "immersion"}
    # The common injury reducer validates its intermediate checkpoint. Detach only
    # this swarm while its HP changes, then reattach the synchronized typed state.
    injury_seed = state.model_copy(
        update={"swarms": tuple(entry for entry in state.swarms if entry.id != swarm.id)}
    )
    updated, result = apply_injury(
        injury_seed,
        Wound(
            id=EVENT_PREFIX + "swarm-injury:" + hashlib.sha256(command.id.encode()).hexdigest(),
            actor_id=swarm.actor_id,
            expected_revision=state.revision,
            basic_damage=basic,
            resistance=0,
            damage_type=command.damage_type,
            injury_source="area" if area else "attack",
        ),
        ht=10,
        rng=rng,
        system=True,
    )
    after = _hp(updated, swarm.actor_id)
    remaining = max(0, after.current)
    if after.current != remaining:
        updated = updated.model_copy(
            update={
                "pools": tuple(
                    entry.model_copy(update={"current": remaining})
                    if entry.id == after.id
                    else entry
                    for entry in updated.pools
                )
            }
        )
    dispersed = remaining == 0
    changed = Swarm.model_validate(
        {
            **swarm.model_dump(),
            "remaining_hp": remaining,
            "active": not dispersed,
            "dispersed_by_command_id": command.id if dispersed else None,
            "occupants": () if dispersed else swarm.occupants,
        }
    )
    updated = _replace_swarm(updated, changed)
    return updated, SwarmDamageOutcome(
        swarm_id=swarm.id,
        countermeasure=command.countermeasure,
        ignored=False,
        injury=result,
        remaining_hp=remaining,
        dispersed=dispersed,
    )


def apply_creature_combat(
    state: ResourceState,
    command: CreatureCombatCommand,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, CreatureCombatOutcome]:
    """Apply one exact-once creature/swarm command at the resource CAS boundary."""
    if not system:
        raise ValidationError("Creature combat transitions require engine authority")
    previous = _previous(state, command)
    if previous is not False:
        return state, previous
    if command.expected_revision != state.revision:
        raise ConflictError("Creature combat resource revision changed")
    if isinstance(command, ResolveNaturalAttack):
        return resolve_natural_attack(state, command, rng=rng, system=True)
    if isinstance(command, SetSwarmArea):
        updated = _set_swarm_area(state, command)
        return _append_event(updated, command, None, command.swarm_id), None
    swarm_outcome: SwarmTurnOutcome | SwarmDamageOutcome
    if isinstance(command, ResolveSwarmTurn):
        updated, swarm_outcome = _resolve_swarm_turn(state, command, rng)
    else:
        updated, swarm_outcome = _damage_swarm(state, command, rng)
    return (
        _append_event(updated, command, swarm_outcome, command.swarm_id),
        swarm_outcome,
    )


def replay_creature_combat(
    seed: ResourceState,
    commands: tuple[CreatureCombatCommand, ...],
    *,
    rng: RandomSource,
) -> ResourceState:
    state = seed
    for command in commands:
        state, _ = apply_creature_combat(state, command, rng=rng, system=True)
    return state
