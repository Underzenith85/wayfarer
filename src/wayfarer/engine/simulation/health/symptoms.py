"""Causal Symptoms activation and recovery; no hit from another source counts twice."""

import hashlib

from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def _refresh(state: ResourceState) -> ResourceState:
    pools = {p.id: p for p in state.pools}
    effects = []
    for effect in state.symptom_effects:
        outstanding = sum(
            d.remaining
            for d in state.symptom_debts
            if d.pool_id == effect.pool_id and d.source_id == effect.source_id
        )
        boundary = pools[effect.pool_id].maximum * effect.spec.numerator
        scaled = outstanding * effect.spec.denominator
        # B109: exceeds activates; healing past the boundary removes it.
        active = scaled >= boundary if effect.active else scaled > boundary
        effects.append(effect.model_copy(update={"active": active}))
    return state.model_copy(update={"symptom_effects": tuple(effects)})


def track_damage(
    state: ResourceState, *, pool_id: str, injury_id: str, amount: int
) -> ResourceState:
    if not amount or not any(e.pool_id == pool_id for e in state.symptom_effects):
        return state
    if any(d.id == injury_id for d in state.symptom_debts):
        return state
    return state.model_copy(
        update={
            "symptom_debts": state.symptom_debts
            + (SymptomDebt(id=injury_id, pool_id=pool_id, remaining=amount),)
        }
    )


def register(
    state: ResourceState,
    *,
    actor_id: str,
    source_id: str,
    injury_id: str,
    amount: int,
    pool_id: str,
    spec: SymptomSpec,
    restriction_id: str | None = None,
) -> ResourceState:
    pool = next((p for p in state.pools if p.id == pool_id), None)
    if pool is None or not pool_id.endswith(":" + actor_id):
        raise ValidationError("Symptoms requires its canonical HP or FP pool")
    effects = state.symptom_effects
    debts = state.symptom_debts
    if not any(e.pool_id == pool_id for e in effects):
        previous_loss = max(0, pool.maximum - pool.current - amount)
        if previous_loss:
            debts += (
                SymptomDebt(
                    id="symptoms-baseline:" + pool_id, pool_id=pool_id, remaining=previous_loss
                ),
            )
    identifier = "symptoms:" + hashlib.sha256(f"{pool_id}:{source_id}".encode()).hexdigest()
    existing = next((e for e in effects if e.id == identifier), None)
    if existing and existing.spec != spec:
        raise ValidationError("Symptoms source changed its approved effect")
    if existing is None:
        effects += (
            SymptomEffect(
                id=identifier, pool_id=pool_id, source_id=source_id, actor_id=actor_id, spec=spec
            ),
        )
    if amount:
        debts = tuple(d for d in debts if d.id != injury_id) + (
            SymptomDebt(
                id=injury_id,
                pool_id=pool_id,
                source_id=source_id,
                remaining=amount,
                restriction_id=restriction_id,
            ),
        )
    return _refresh(state.model_copy(update={"symptom_effects": effects, "symptom_debts": debts}))


def reconcile_recovery(before: ResourceState, state: ResourceState) -> ResourceState:
    if not state.symptom_effects:
        return state
    old = {p.id: p.current for p in before.pools}
    earned = {p.id: max(0, p.current - old.get(p.id, p.current)) for p in state.pools}
    protected = {i.id for i in state.illnesses if i.active}
    debts = []
    for debt in state.symptom_debts:
        eligible = 0 if debt.restriction_id in protected else earned.get(debt.pool_id, 0)
        healed = min(debt.remaining, eligible)
        earned[debt.pool_id] = earned.get(debt.pool_id, 0) - healed
        debts.append(debt.model_copy(update={"remaining": debt.remaining - healed}))
    return _refresh(state.model_copy(update={"symptom_debts": tuple(debts)}))
