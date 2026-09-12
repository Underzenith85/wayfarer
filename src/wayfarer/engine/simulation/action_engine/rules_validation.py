"""Checking that a scenario's rules package is one this engine can play."""

from __future__ import annotations

from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus
from wayfarer.engine.simulation.ability_types import AbilityRules
from wayfarer.engine.simulation.actions import ActionRules, CheckRule, PlayState
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.magic.bindings import SpellRules
from wayfarer.engine.simulation.resources import ResourceEngine
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError


def _validate_spell_rules(reviewer: PowerReviewer, spells: SpellRules) -> None:
    """Spell channels bind to pinned training definitions of the compiled profile."""
    from wayfarer.engine.rules.magic.gurps_magic import validate_definitions
    from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
    from wayfarer.engine.rules.skills.mundane.ranged import PROCEDURES as RANGED_PROCEDURES

    definitions = reviewer.compiler.definitions
    validate_definitions(reviewer.compiler.statistics_profile, definitions)
    if reviewer.compiler.statistics_profile != spells.profile_id:
        raise ValidationError("Spell rules require the exact compiled profile")
    # #361 reconciles the adapter's projectile skill with the mundane
    # inventory's Projectile specialty. Both are pinned definitions of
    # the same B201 row, so either satisfies the channel; nothing else
    # does, and no existing pin changes.
    projectile = RANGED_PROCEDURES["skill:innate-attack-projectile"]
    if any(c.spell_id == "fireball" for c in spells.channels) and definitions.get(
        "skill:innate-attack-projectile"
    ) not in (projectile_definition(), projectile.definition()):
        raise ValidationError("Fireball requires the pinned projectile skill")
    if any("spell:" + c.spell_id not in definitions for c in spells.channels):
        raise ValidationError("Spell channels require pinned training definitions")


def _validate_ability_rules(reviewer: PowerReviewer, abilities: AbilityRules) -> None:
    """Ability runtime metadata, cost and status must match the pinned catalog."""
    from wayfarer.engine.rules.supernatural.abilities import definition as ability_definition
    from wayfarer.engine.rules.supernatural.abilities import metadata, validate_binding
    from wayfarer.engine.rules.traits.base import TraitOptions

    definitions = reviewer.compiler.definitions
    if reviewer.compiler.statistics_profile != abilities.profile_id:
        raise ValidationError("Ability rules require the exact compiled profile")
    for ability in abilities.abilities:
        definition = definitions.get(ability.definition_id)
        if definition is None or definition.trait_rules != metadata(ability):
            raise ValidationError("Ability runtime metadata differs from pinned catalog")
        expected = ability_definition(ability)
        if (
            definition.point_cost != expected.point_cost
            or definition.status != expected.status
            or "supernatural" not in definition.hooks
        ):
            raise ValidationError("Ability cost or implementation status differs from runtime")
        validate_binding(ability, 1, TraitOptions(modifiers=ability.modifiers))


def _index_checks(checks: tuple[CheckRule, ...]) -> dict[tuple[str, str], CheckRule]:
    """Each check id is unique and each (action, target) pair has exactly one check."""
    by_key: dict[tuple[str, str], CheckRule] = {}
    ids: set[str] = set()
    for rule in checks:
        if rule.id in ids:
            raise ValidationError(
                f"Ambiguous action check rules: {rule.id} is declared twice", reference=rule.id
            )
        key = (rule.action, rule.target_id)
        if key in by_key:
            raise ValidationError(
                f"Ambiguous action check rules: {by_key[key].id} and {rule.id} both "
                f"{rule.action} {rule.target_id}; one check per action and target",
                reference=rule.id,
            )
        by_key[key] = rule
        ids.add(rule.id)
    return by_key


def _validate_check_rules(
    reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
) -> None:
    """Checks, consumables and ruling alternatives resolve to implemented, pinned rows.

    Every failure names the offending check or alternative so authors and the
    scenario studio can localise it without reading the engine (#365).
    """
    definitions = reviewer.compiler.definitions
    for rule in rules.checks:
        definition = definitions.get(rule.definition_id)
        if definition is None:
            reason = f"{rule.definition_id} is not in the pinned catalog"
        elif definition.kind is not DefinitionKind.SKILL:
            reason = f"{rule.definition_id} is {definition.kind.value}, not a skill"
        elif definition.status is not ImplementationStatus.IMPLEMENTED:
            reason = f"{rule.definition_id} is {definition.status.value}"
        else:
            reason = None
        if reason is not None:
            raise ValidationError(
                f"Action check {rule.id} requires an implemented catalog skill; {reason}",
                reference=rule.id,
            )
        if (rule.package_id, rule.package_version) != reviewer.compiler.definition_packages[
            rule.definition_id
        ]:
            raise ValidationError(
                f"Check provenance is not pinned for {rule.id}: {rule.definition_id} comes from "
                f"{reviewer.compiler.definition_packages[rule.definition_id]}",
                reference=rule.id,
            )
        if rule.action == "social" and rule.definition_id != "skill:diplomacy":
            raise ValidationError(
                f"Only diplomacy social checks are implemented; {rule.id} uses "
                f"{rule.definition_id}",
                reference=rule.id,
            )
        if rule.required_equipment is not None and rule.required_equipment not in resources.specs:
            raise ValidationError(
                f"Unknown required equipment {rule.required_equipment} on check {rule.id}",
                reference=rule.id,
            )
    for key in rules.consumables:
        if key not in resources.specs:
            raise ValidationError(f"Unknown consumable definition {key}", reference=key)
    if rules.adjudication is not None:
        by_id = {r.id: r for r in rules.checks}
        for alternative in rules.adjudication.alternatives:
            check = by_id.get(alternative.check_rule_id)
            if check is None or check.action != "social":
                raise ValidationError(
                    f"Ruling requires an existing social check; alternative {alternative.id} "
                    f"names {alternative.check_rule_id}",
                    reference=alternative.id,
                )
            if not -20 <= check.modifier + alternative.modifier <= 20:
                raise ValidationError(
                    f"Combined ruling modifier exceeds engine bounds for alternative "
                    f"{alternative.id} on check {check.id}",
                    reference=alternative.id,
                )


def _validate_gurps_equipment(
    reviewer: PowerReviewer, resources: ResourceEngine, combat: CombatRules
) -> None:
    """GURPS equipment replaces prototype profiles and pins every referenced skill."""
    equipment = combat.gurps_equipment
    if equipment is None:
        return
    definitions = reviewer.compiler.definitions
    if reviewer.compiler.statistics_profile != equipment.profile_id:
        raise ValidationError("Combat and compiled statistics profiles must match")
    if combat.attacks or combat.protection:
        raise ValidationError("GURPS combat cannot use prototype attack profiles")
    for entry in equipment.entries:
        definition = definitions.get(entry.definition_id)
        if (
            definition is None
            or definition.kind is not DefinitionKind.EQUIPMENT
            or definition.source_id != entry.provenance.source_id
        ):
            raise ValidationError("Combat equipment source does not match pinned definitions")
        referenced = tuple(m.skill_id for m in entry.modes) + (
            (entry.shield.skill_id,) if entry.shield else ()
        )
        if any(
            key not in definitions or definitions[key].kind is not DefinitionKind.SKILL
            for key in referenced
        ):
            raise ValidationError("Combat equipment requires pinned weapon/shield skills")
    if resources.specs != {e.definition_id: e.inventory_spec() for e in equipment.entries}:
        raise ValidationError("Combat requires exact profile inventory specifications")


def _validate_world_references(rules: ActionRules, state: PlayState) -> None:
    """Entity locations are locations and scenario checks target real entities and facts."""
    entities = {e.id: e for e in state.world.entities}
    for entity in state.world.entities:
        if (
            entity.location_id is not None
            and entities[entity.location_id].kind is not EntityKind.LOCATION
        ):
            raise ValidationError("Entity location must reference a location")
    facts = {f.id for f in state.world.facts}
    for rule in rules.checks:
        if rule.target_id not in entities:
            raise ValidationError(
                f"Invalid scenario check references: {rule.id} targets unknown entity "
                f"{rule.target_id}",
                reference=rule.id,
            )
        unknown = sorted(set(rule.reveal_fact_ids) - facts)
        if unknown:
            raise ValidationError(
                f"Invalid scenario check references: {rule.id} reveals unknown facts "
                f"{', '.join(unknown)}",
                reference=rule.id,
            )
        if rule.action == "social" and entities[rule.target_id].kind is not EntityKind.ACTOR:
            raise ValidationError(
                f"Social target must be an actor; {rule.id} targets {rule.target_id}",
                reference=rule.id,
            )
