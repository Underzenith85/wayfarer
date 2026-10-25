"""B376 failed heavy barehand Parry and its fixed-yard/DX consequences."""

import hashlib
from decimal import Decimal
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Modifier
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.combat.displacement import (
    DisplacementResult,
    apply_displacement,
    require_clear_step,
)
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.generations import staff_heavy_bare_parry_enabled
from wayfarer.engine.simulation.combat.melee.modes import heavy_parry_weight
from wayfarer.engine.simulation.equipment.catalog import MeleeMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class HeavyBareParryResult(Record):
    pending_id: str
    defender_id: str
    incoming_weight: int
    basic_lift_millipounds: Decimal
    automatic_failure: Literal[True] = True
    displacement: DisplacementResult


def exceeds_lift(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    participant: Combatant,
    incoming: MeleeMode,
) -> bool:
    pending = encounter.pending_defense
    assert pending is not None
    compiled = build(runtime, state, participant.actor_id, defensive=True)
    assert compiled.statistics is not None
    weight = heavy_parry_weight(runtime, state, participant, pending.weapon_id, incoming.id)
    return weight is not None and weight > compiled.statistics.basic_lift * 1000


def require_heavy_bare_parry(
    runtime: RulesContext, state: PlayState, encounter: Encounter, participant: Combatant
) -> None:
    if not staff_heavy_bare_parry_enabled():
        raise ValidationError("Held Melee contact does not yet support heavy barehanded parries")
    pending = encounter.pending_defense
    assert pending is not None
    hp = next(p for p in state.resources.pools if p.id == "hp:" + participant.actor_id)
    fp = next(p for p in state.resources.pools if p.id == "fp:" + participant.actor_id)
    if (
        hp.injury is None
        or hp.injury.anatomy != "human"
        or hp.injury.incapacitated
        or hp.current <= 0
        or fp.current <= 0
        or participant.posture != "standing"
        or participant.personal_flight is not None
        or participant.retreat_used
        or any(participant.actor_id in (g.holder_id, g.target_id) for g in encounter.grips)
        or any(participant.actor_id in (m.mount_id, m.rider_id) for m in encounter.mounted_combat)
    ):
        raise ValidationError(
            "Heavy barehand Parry requires a standing unrestrained human defender"
        )
    require_clear_step(
        runtime, encounter, source_id=pending.attacker_id, target_id=participant.actor_id
    )


def settle_heavy_bare_parry(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    incoming: MeleeMode,
    enabled: bool,
    admitted_basic_lift: Decimal,
) -> tuple[PlayState, Encounter, tuple[CheckTrace, ...]]:
    if not enabled:
        return state, encounter, ()
    pending = encounter.pending_defense
    assert pending is not None
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    compiled = build(runtime, state, defender.actor_id, defensive=True)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == "hp:" + defender.actor_id)
    assert hp.injury is not None
    fallen = hp.injury.prone
    checks: tuple[CheckTrace, ...] = ()
    if not hp.injury.incapacitated and fatigue_ready(state, defender.actor_id):
        balance = (
            4
            if any(p.definition_id == "trait:advantage:perfect-balance" for p in compiled.purchases)
            else 0
        )
        trace = success_roll(
            "gurps-basic-set-4e-2004",
            compiled.statistics.dx,
            check_modifiers(state.resources, defender.actor_id, "dx", defensive=True)
            + (
                (Modifier(balance, "Perfect Balance", "basic-set:B74", "characters-third"),)
                if balance
                else ()
            ),
            rng=runtime.rng,
        )
        checks = (trace,)
        fallen = fallen or not trace.outcome.succeeded
    state, encounter, displacement = apply_displacement(
        runtime,
        state,
        encounter,
        source_id=pending.attacker_id,
        target_id=defender.actor_id,
        yards=1,
        fallen=fallen,
        checks=checks,
    )
    weight = heavy_parry_weight(runtime, state, defender, pending.weapon_id, incoming.id)
    assert weight is not None
    result = HeavyBareParryResult(
        pending_id=pending.id,
        defender_id=defender.actor_id,
        incoming_weight=weight,
        basic_lift_millipounds=admitted_basic_lift * 1000,
        displacement=displacement,
    )
    event = ResourceEvent(
        id="melee-spell:heavy-bare-parry:" + hashlib.sha256(pending.id.encode()).hexdigest(),
        at=state.resources.game_time,
        target_id=defender.actor_id,
        kind=result.model_dump_json(),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    )
    return state, encounter, checks
