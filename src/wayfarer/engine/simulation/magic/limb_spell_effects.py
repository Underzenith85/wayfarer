"""Independent B240–242 resisted contact roll for B244 Paralyze Limb."""

from typing import Literal

from wayfarer.engine.character.traits.mana_divine import mana_divine_traits
from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest, success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.spells import _casting_modifiers
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.models import Record


class LimbContactRoll(Record):
    outcome: Literal["contact-failed", "resisted", "paralyzed"]
    contact_check: CheckTrace
    resistance_check: CheckTrace | None = None
    contest_winner: str | None = None


def resolve_contact(
    runtime: RulesContext, state: PlayState, *, caster_id: str, target_id: str, skill: int
) -> LimbContactRoll:
    target = build(runtime, state, target_id)
    assert target.statistics is not None
    traits = mana_divine_traits(target, runtime.reviewer.compiler.definitions)
    ht_modifiers = check_modifiers(state.resources, target_id, "ht") + (
        Modifier(
            traits.magic_resistance_modifier(),
            "Magic Resistance/susceptibility",
            "B67",
            "characters-third",
            ModifierKind.TRAIT,
        ),
    )
    resistance = target.statistics.ht + sum(m.value for m in ht_modifiers)
    casting_modifiers = _casting_modifiers(state.resources, caster_id, check_symptoms=True) + (
        Modifier(
            traits.magic_casting_modifier(),
            "Subject Magic Resistance/susceptibility",
            "B67",
            "characters-third",
            ModifierKind.TRAIT,
        ),
    )
    uncapped = skill + sum(m.value for m in casting_modifiers)
    effective = min(uncapped, max(16, resistance))
    if effective < uncapped:
        casting_modifiers += (
            Modifier(
                effective - uncapped,
                "Rule of 16",
                "B349",
                "campaigns-fourth",
                ModifierKind.RULE_OF_16,
            ),
        )
    # B349 limits the actual contact roll, not the earlier self-charge roll.
    contact = success_roll("gurps-basic-set-4e-2004", skill, casting_modifiers, rng=runtime.rng)
    if not contact.outcome.succeeded:
        return LimbContactRoll(outcome="contact-failed", contact_check=contact)
    if contact.outcome is Outcome.CRITICAL_SUCCESS:
        return LimbContactRoll(outcome="paralyzed", contact_check=contact, contest_winner="caster")
    resist = success_roll(
        "gurps-basic-set-4e-2004", target.statistics.ht, ht_modifiers, rng=runtime.rng
    )
    contest = resolve_quick_contest(
        "gurps-basic-set-4e-2004",
        Contestant("caster", effective),
        Contestant("subject", resist.effective_target),
        first_dice=contact.dice,
        second_dice=resist.dice,
    )
    return LimbContactRoll(
        outcome="paralyzed" if contest.winner == "caster" else "resisted",
        contact_check=contact,
        resistance_check=resist,
        contest_winner=contest.winner,
    )
