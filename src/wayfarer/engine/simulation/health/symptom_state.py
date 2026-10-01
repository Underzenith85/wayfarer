"""Pure views of active Symptoms; approved purchases remain immutable."""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from wayfarer.engine.character.compiler import DerivedSheet, ValidatedBuild
from wayfarer.engine.character.statistics import DiceExpression, basic_lift, damage
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.effects import ExplanationEntry, Operation
from wayfarer.engine.rules.types.symptoms import SymptomEffect
from wayfarer.engine.simulation.resources import ResourceState


def active(state: ResourceState, actor_id: str) -> tuple[SymptomEffect, ...]:
    return tuple(e for e in state.symptom_effects if e.actor_id == actor_id and e.active)


def penalties(state: ResourceState, actor_id: str) -> dict[str, int]:
    result = dict.fromkeys(("st", "dx", "iq", "ht"), 0)
    for effect in active(state, actor_id):
        spec = effect.spec
        if spec.kind == "attribute-penalty" and spec.attribute is not None:
            result[spec.attribute] = max(result[spec.attribute], spec.level)
    result["will"] = result["per"] = result["iq"]
    return result


def projected_build(
    state: ResourceState,
    actor_id: str,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
) -> ValidatedBuild:
    if build.statistics is None or not active(state, actor_id):
        return build
    changes = penalties(state, actor_id)
    stats = build.statistics
    st = stats.st - changes["st"]
    thrust, swing = (
        damage(stats.profile_id, st) if st > 0 else (DiceExpression(0, 0), DiceExpression(0, 0))
    )
    stats = replace(
        stats,
        st=st,
        dx=stats.dx - changes["dx"],
        iq=stats.iq - changes["iq"],
        ht=stats.ht - changes["ht"],
        will=stats.will - changes["will"],
        per=stats.per - changes["per"],
        basic_lift=basic_lift(stats.profile_id, st) if st > 0 else Decimal(0),
        thrust=thrust,
        swing=swing,
    )
    values = []
    for value in build.sheet.values:
        definition = definitions.get(value.target)
        attribute = (
            str(definition.skill.attribute).lower()
            if definition and definition.skill
            else value.target.split(":")[-1]
        )
        delta = (
            changes.get(attribute, 0)
            if value.target.startswith(("attribute:", "skill:", "secondary:will", "secondary:per"))
            else 0
        )
        replacement = (
            stats.basic_lift if value.target == "secondary:basic-lift" else value.value - delta
        )
        explanations = value.explanations
        if replacement != value.value:
            explanations += (
                ExplanationEntry(
                    "symptoms:" + actor_id,
                    "B109",
                    "characters-third",
                    Operation.ADD,
                    value.value,
                    replacement - value.value,
                    replacement,
                ),
            )
        values.append(replace(value, value=replacement, explanations=explanations))
    return replace(build, statistics=stats, sheet=DerivedSheet(tuple(values)))
