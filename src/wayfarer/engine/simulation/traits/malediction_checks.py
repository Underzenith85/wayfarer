"""B106 caster check before its B348 resistance, sharing canonical adjudication."""

from __future__ import annotations

from collections.abc import Mapping
from math import ceil
from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.sensory import sensory_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Modifier, RandomSource
from wayfarer.engine.rules.conformance import require_capabilities
from wayfarer.engine.rules.gurps_checks import (
    QUICK_CONTEST_CAPABILITY,
    RESISTANCE_CAPABILITY,
    SUCCESS_CAPABILITIES,
    Contestant,
    _decide_quick,
    rule_of_16_modifier,
)
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.symptom_state import active
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.traits.composed_attacks import AttackCompositionContext

PROFILE = "gurps-basic-set-4e-2004"


class MaledictionPreparation(Record):
    attacker: Contestant
    resister: Contestant | None
    spec: AttackRollSpec

    @property
    def resist(self) -> bool:
        return self.resister is not None


def prepare_malediction(
    state: ResourceState,
    actor_id: str,
    build: ValidatedBuild,
    target: ValidatedBuild,
    profile: AttackProfile,
    context: AttackCompositionContext,
    definitions: Mapping[str, RuleDefinition],
    *,
    current_conditions: bool = False,
) -> MaledictionPreparation:
    assert build.statistics is not None and target.statistics is not None
    if context.maneuver != "concentrate":
        raise ValidationError("Malediction requires Concentrate")
    if not context.target_perceived:
        raise ValidationError("Malediction requires a clearly perceived target")
    if profile.penetration_sense == "vision" and (
        not context.vision_contact
        or any(
            e.spec.kind == "blindness"
            for actor in (actor_id, context.target_id)
            for e in active(state, actor)
        )
    ):
        raise ValidationError("Vision-Based Malediction requires the victim's available vision")
    penalties: tuple[Modifier, ...] = (
        Modifier(
            -ceil(context.distance_yards), "Malediction 1 range", "B9/B106", "characters-third"
        ),
    )
    if current_conditions:
        penalties += check_modifiers(state, actor_id, "will")
    attacker = Contestant(actor_id, build.statistics.will, penalties)
    if not context.resist:
        return MaledictionPreparation(
            attacker=attacker,
            resister=None,
            spec=AttackRollSpec(
                profile_id=PROFILE, target=attacker.base_target, modifiers=penalties
            ),
        )
    require_capabilities(
        PROFILE, (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY, RESISTANCE_CAPABILITY)
    )
    resister = prepare_resister(
        state, target, profile, context, definitions, current_conditions=current_conditions
    )
    if attacker.id == resister.id:
        raise ValidationError("Contestants must have distinct identifiers")
    return MaledictionPreparation(
        attacker=attacker,
        resister=resister,
        spec=AttackRollSpec(
            profile_id=PROFILE,
            target=attacker.base_target,
            modifiers=penalties + rule_of_16_modifier(PROFILE, attacker, resister),
            rule_id=RESISTANCE_CAPABILITY,
        ),
    )


def prepare_resister(
    state: ResourceState,
    target: ValidatedBuild,
    profile: AttackProfile,
    context: AttackCompositionContext,
    definitions: Mapping[str, RuleDefinition],
    *,
    current_conditions: bool = False,
) -> Contestant:
    """Read only the still-unrolled target's current resistance facts."""
    assert target.statistics is not None
    return Contestant(
        context.target_id,
        target.statistics.will,
        (
            (Modifier(5, "Protected vision", "B78/B109", "characters-third"),)
            if profile.penetration_sense == "vision"
            and sensory_traits(target, definitions).protected("vision")
            else ()
        )
        + (
            check_modifiers(state, context.target_id, "will", defensive=True)
            if current_conditions
            else ()
        ),
    )


def resolve_malediction(
    prepared: MaledictionPreparation,
    *,
    rng: RandomSource,
    selected_attack: CheckTrace | None = None,
) -> tuple[bool, tuple[CheckTrace, ...]]:
    attack = selected_attack or prepared.spec.roll(rng)
    if prepared.spec.score(attack.dice) != attack:
        raise ValidationError("Malediction choice differs from its captured caster target")
    resister = prepared.resister
    if resister is None or selected_attack is not None and not attack.outcome.succeeded:
        return attack.outcome.succeeded, (attack,)
    resistance = AttackRollSpec(
        profile_id=PROFILE,
        target=resister.base_target,
        modifiers=resister.modifiers,
        rule_id=RESISTANCE_CAPABILITY,
    ).roll(rng)
    contest = _decide_quick(PROFILE, prepared.attacker, resister, attack, resistance)
    return attack.outcome.succeeded and contest.winner == prepared.attacker.id, (attack, resistance)
