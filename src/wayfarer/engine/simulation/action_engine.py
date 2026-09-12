"""Feasibility, deterministic resolution and invariants for typed actions.

``ActionEngine`` is the verb over the play nouns declared in
``wayfarer.engine.simulation.actions``; it never defines state of its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild, pool_limits
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import SKILLS, DefinitionKind, ImplementationStatus
from wayfarer.engine.rules.checks import (
    NO_RANDOM,
    CheckTrace,
    Modifier,
    Outcome,
    RandomSource,
    success_check,
)
from wayfarer.engine.rules.effects import DerivedValue, EffectEvaluator, MechanicalTarget
from wayfarer.engine.rules.hazard_types import require_hazards_settled
from wayfarer.engine.rules.location_types import disabled_locations
from wayfarer.engine.simulation.ability_types import AbilityRules
from wayfarer.engine.simulation.ability_types import validate_channels as validate_ability_channels
from wayfarer.engine.simulation.access import validate_members
from wayfarer.engine.simulation.actions import (
    ACTION_ADAPTER,
    ActionResult,
    ActionRules,
    Attack,
    CheckRule,
    Inspect,
    Move,
    PlayState,
    Question,
    Social,
    TypedAction,
    UseItem,
    Wait,
)
from wayfarer.engine.simulation.adjudication import expire_rulings
from wayfarer.engine.simulation.advancement import validate_ledgers
from wayfarer.engine.simulation.combat import CombatEngine, CombatRules, validate_consequences
from wayfarer.engine.simulation.condition_checks import definition_modifiers
from wayfarer.engine.simulation.events import (
    ActionResolved,
    ActorAudience,
    EngineEvent,
    play_events,
)
from wayfarer.engine.simulation.noncombat import validate_state as validate_noncombat_state
from wayfarer.engine.simulation.party import validate as validate_party
from wayfarer.engine.simulation.party import validate_effects as validate_party_effects
from wayfarer.engine.simulation.resources import Advance, Consume, ResourceEngine
from wayfarer.engine.simulation.scenes import JournalEntry, SceneEvent
from wayfarer.engine.simulation.scenes import validate_state as validate_scene_state
from wayfarer.engine.simulation.spell_bindings import SpellRules
from wayfarer.engine.simulation.spell_bindings import validate_channels as validate_spell_channels
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError


def _validate_spell_rules(reviewer: PowerReviewer, spells: SpellRules) -> None:
    """Spell channels bind to pinned training definitions of the compiled profile."""
    from wayfarer.engine.rules.gurps_magic import validate_definitions
    from wayfarer.engine.rules.mundane_skills.ranged import PROCEDURES as RANGED_PROCEDURES
    from wayfarer.engine.rules.spell_catalog import projectile_definition

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
    from wayfarer.engine.rules.abilities import definition as ability_definition
    from wayfarer.engine.rules.abilities import metadata, validate_binding
    from wayfarer.engine.rules.traits import TraitOptions

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


def _configuration_digest(
    reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
) -> str:
    """Pin the rules, policy, effects and equipment a checkpoint was produced under."""
    # Preserve the exact Wave 7 digest for campaigns that have not enabled
    # adjudication. Enabling or changing policy requires explicit migration.
    excluded = {
        field
        for field, disabled in (
            ("adjudication", rules.adjudication is None),
            ("combat", rules.combat is None),
            ("scenes", rules.scenes is None),
        )
        if disabled
    }
    encoded_rules = rules.model_dump_json(exclude=excluded)
    payload = (
        encoded_rules
        + (rules.scenes.model_dump_json() if rules.scenes is not None else "")
        + (
            "".join(
                p.model_dump_json()
                for p in (
                    *rules.combat.attacks,
                    *rules.combat.protection,
                    *rules.combat.consequences,
                )
            )
            if rules.combat is not None
            else ""
        )
        + (rules.objectives.model_dump_json() if rules.objectives else "")
        + (
            rules.combat.gurps_equipment.model_dump_json()
            if rules.combat and rules.combat.gurps_equipment
            else ""
        )
        + (rules.noncombat.model_dump_json() if rules.noncombat else "")
        + (rules.party.model_dump_json() if rules.party else "")
        + (rules.npcs.model_dump_json() if rules.npcs else "")
        + (rules.recovery.model_dump_json() if rules.recovery else "")
        + (rules.abilities.model_dump_json() if rules.abilities else "")
        + (rules.spells.model_dump_json() if rules.spells else "")
        + reviewer.policy.digest
        + repr(resources.rules)
        + repr(reviewer.compiler.effects)
        + "".join(spec.model_dump_json() for _, spec in sorted(resources.specs.items()))
    )
    return hashlib.sha256(payload.encode()).hexdigest()


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


class ActionEngine:
    def __init__(
        self, reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
    ) -> None:
        # Retain explicitly selected server policy versions without widening v1 schemas.
        rules = type(rules).model_validate(rules)
        if reviewer.compiler.rules != resources.rules:
            raise ValidationError("Character and resource rules differ")
        self.reviewer, self.resources, self.rules = reviewer, resources, rules
        self.checks = _index_checks(rules.checks)
        if rules.spells is not None:
            _validate_spell_rules(reviewer, rules.spells)
        if rules.abilities is not None:
            _validate_ability_rules(reviewer, rules.abilities)
        _validate_check_rules(reviewer, resources, rules)
        self.combat = CombatEngine(rules.combat, resources) if rules.combat is not None else None
        if rules.combat is not None:
            _validate_gurps_equipment(reviewer, resources, rules.combat)
        self.digest = _configuration_digest(reviewer, resources, rules)

    def validate(self, state: PlayState) -> None:
        """Check every aggregate invariant of a checkpoint against this configuration."""
        rules = self.rules
        if state.configuration_digest != self.digest:
            raise ValidationError("Play configuration changed; explicit migration required")
        if state.revision != state.resources.revision:
            raise ValidationError("Play and resource revisions diverged")
        from wayfarer.engine.simulation.encounter_context import validate_contexts
        from wayfarer.engine.simulation.firearms import validate_failures
        from wayfarer.engine.simulation.lifecycle import validate_lifecycle

        validate_failures(state.resources, rules.combat.gurps_equipment if rules.combat else None)
        if rules.spells:
            validate_spell_channels(rules.spells, state)
        if rules.abilities:
            validate_ability_channels(rules.abilities, state)
        if rules.combat:
            validate_consequences(rules.combat, state)
        if rules.objectives is not None:
            rules.objectives.validate_state(state, frozenset(self.resources.specs))
        scene_ids = frozenset(s.id for s in rules.scenes.scenes) if rules.scenes else frozenset()
        validate_noncombat_state(
            rules.noncombat,
            state,
            check_ids=frozenset(c.id for c in rules.checks),
            scene_ids=scene_ids,
        )
        if rules.party is not None:
            validate_party_effects(rules.party, state, scene_ids)
        validate_lifecycle(state, rules)
        validate_party(state)
        self.validate_rulings(state)
        state.world.validate()
        self.resources.validate(state.resources)
        validate_scene_state(rules.scenes, state)
        validate_ledgers(state)
        validate_members(state)
        if len({e.id for e in state.encounters}) != len(state.encounters):
            raise ValidationError("Duplicate encounter ID")
        for encounter in state.encounters:
            if self.combat is None:
                raise ValidationError("Campaign has encounters without combat rules")
            self.combat.validate(
                encounter,
                state.world,
                state.resources,
                frozenset(actor.actor_id for actor in state.actors),
            )
        validate_contexts(state, rules.scenes, rules.combat)
        initiatives = self._validate_actor_builds(state)
        if any(
            participant.initiative != initiatives[participant.actor_id]
            for encounter in state.encounters
            for participant in encounter.participants
        ):
            raise ValidationError("Combat initiative disagrees with compiled character")
        _validate_world_references(rules, state)

    def _validate_actor_builds(self, state: PlayState) -> dict[str, int]:
        """Every play actor is a legal, approved build whose resources match; returns DX."""
        from wayfarer.engine.character.physical_traits import physical_traits

        entities = {e.id: e for e in state.world.entities}
        actors = {a.actor_id: a for a in state.actors}
        if len(actors) != len(state.actors) or not set(actors) <= self.resources.actors:
            raise ValidationError("Invalid play actor IDs")
        owners = {o.actor_id: o for o in state.resources.owners}
        pools = {p.id: p for p in state.resources.pools}
        initiatives: dict[str, int] = {}
        for actor in state.actors:
            build = self.reviewer.compiler.compile(actor.proposal.draft).build
            if build is None:
                raise ValidationError("Play actor has an illegal build")
            owner = owners.get(actor.actor_id)
            if owner is None or set(owner.definitions) != {
                p.definition_id for p in build.purchases
            }:
                raise ValidationError("Resource prerequisites do not match the compiled build")
            values = {v.target: v.value for v in build.sheet.values}
            initiatives[actor.actor_id] = int(values["attribute:dx"])
            for kind, maximum in pool_limits(build).items():
                pool = pools.get(f"{kind}:{actor.actor_id}")
                if pool is None or pool.maximum != maximum:
                    raise ValidationError("Runtime pool limit does not match the compiled build")
            hp = pools.get(f"hp:{actor.actor_id}")
            if (
                hp is not None
                and hp.injury is not None
                and hp.injury.physical_traits
                != physical_traits(build, self.reviewer.compiler.definitions)
            ):
                raise ValidationError("Physical trait projection does not match the pinned build")
            entity = entities.get(actor.actor_id)
            if entity is None or entity.kind is not EntityKind.ACTOR:
                raise ValidationError("Play actor is not a world actor")
            if any(key not in entities for key in actor.aware_of):
                raise ValidationError("Unknown awareness reference")
            if actor.approval is not None:
                if (
                    actor.approval not in state.approvals
                    or actor.approval.recorded_revision > state.revision
                ):
                    raise ValidationError("Approval is not in the canonical ledger")
                self.reviewer.activate(
                    actor.proposal,
                    actor.approval,
                    campaign_id=state.campaign_id,
                    actor_id=actor.actor_id,
                )
        return initiatives

    def validate_rulings(self, state: PlayState) -> None:
        if len({r.id for r in state.rulings}) != len(state.rulings):
            raise ValidationError("Duplicate ruling ID")
        policy = self.rules.adjudication
        for ruling in state.rulings:
            if (
                policy is None
                or ruling.campaign_id != state.campaign_id
                or ruling.configuration_digest != self.digest
                or ruling.policy_digest != policy.digest
                or ruling.opened_revision > ruling.valid_revision
                or ruling.valid_revision > state.revision
                or ruling.expires_at != ruling.opened_at + policy.lifetime_ticks
                or ruling.opened_at > state.resources.game_time
                or ruling.actor_id not in {a.actor_id for a in state.actors}
            ):
                raise ValidationError("Invalid ruling pins or revision")
            original = ACTION_ADAPTER.validate_json(ruling.original_action_json)
            if (
                not isinstance(original, Social)
                or original.approach == "diplomacy"
                or original.hypothetical
                or original.actor_id != ruling.actor_id
                or original.expected_revision + 1 != ruling.opened_revision
            ):
                raise ValidationError("Invalid original ruling action")
            check = self.checks.get(("social", original.target_id or ""))
            expected = tuple(
                a for a in policy.alternatives if check is not None and a.check_rule_id == check.id
            )
            if not expected or ruling.alternatives != expected:
                raise ValidationError("Ruling alternatives do not match server policy")
            if ruling.status == "pending" and (
                ruling.valid_revision != ruling.opened_revision
                or ruling.selected_id is not None
                or ruling.authority is not None
                or ruling.approver_id is not None
                or ruling.decided_revision is not None
            ):
                raise ValidationError("Pending ruling cannot contain a decision")
            if (
                ruling.decided_revision is not None
                and ruling.valid_revision != ruling.decided_revision
            ):
                raise ValidationError("Ruling consent revision cannot be renewed")
            if ruling.status in ("approved", "executed") or ruling.selected_id is not None:
                selected = next((a for a in expected if a.id == ruling.selected_id), None)
                if selected is None or not ruling.reason or ruling.decided_revision is None:
                    raise ValidationError("Ruling has no recorded approval")
                if not ruling.opened_revision < ruling.decided_revision <= state.revision:
                    raise ValidationError("Invalid ruling decision revision")
                if ruling.authority == "gm":
                    valid = ruling.approver_id in self.reviewer.gm_ids
                elif ruling.authority == "player":
                    valid = policy.player_approval and ruling.approver_id == ruling.actor_id
                else:
                    valid = (
                        ruling.authority == "policy"
                        and ruling.approver_id == "system:adjudication"
                        and policy.automatic
                        and policy.automatic_minimum
                        <= selected.modifier
                        <= policy.automatic_maximum
                    )
                if not valid:
                    raise ValidationError("Invalid ruling approval authority")
            if ruling.status == "executed" and (
                ruling.executed_revision is None
                or ruling.decided_revision is None
                or not ruling.decided_revision < ruling.executed_revision <= state.revision
            ):
                raise ValidationError("Invalid ruling execution revision")

    @staticmethod
    def _location(entity: Entity) -> str | None:
        return entity.id if entity.kind is EntityKind.LOCATION else entity.location_id

    def assess(self, state: PlayState, command: TypedAction) -> ActionResult:
        if command.kind != "question" and (
            command.actor_id in state.recovery.dead_actor_ids
            or any(
                c.actor_id == command.actor_id and c.released_at is None
                for c in state.recovery.captivity
            )
        ):
            raise ValidationError("Setback requires an authored recovery choice")
        self.validate(state)

        def result(
            status: Literal[
                "feasible",
                "clarification",
                "question",
                "rejected",
                "unsupported",
                "adjudication_required",
            ],
            code: str,
        ) -> ActionResult:
            return ActionResult(
                status=status,
                revision=state.revision,
                code=code,
                command_id=command.id,
                rules_digest=self.digest,
            )

        actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
        if actor is None:
            return result("rejected", "actor.unavailable")
        if command.expected_revision != state.revision:
            raise ConflictError("Play revision changed")
        from wayfarer.engine.simulation.encounter_context import activity_for

        if activity_for(state, command.actor_id).encounter is not None:
            return result("rejected", "combat.command_required")
        if isinstance(command, Question) or command.hypothetical:
            return result("question", "action.no_effect")
        from wayfarer.engine.simulation.fright import blocked, requires_adjudication

        if not isinstance(command, Wait) and (
            blocked(state.resources, command.actor_id, kind=command.kind)
            or requires_adjudication(state.resources, command.actor_id)
        ):
            return result("rejected", "actor.fright")
        from wayfarer.engine.simulation.spell_effects import dazed

        if not isinstance(command, Wait) and dazed(state.resources, command.actor_id):
            return result("rejected", "actor.dazed")
        require_hazards_settled(
            state.resources.hazards, frozenset({command.actor_id}), state.resources.game_time
        )
        if actor.approval is None:
            return result("rejected", "character.approval_required")
        pools = {p.id: p for p in state.resources.pools}
        if not isinstance(command, Wait) and pools[f"hp:{actor.actor_id}"].current == 0:
            return result("rejected", "actor.incapacitated")
        if (
            not isinstance(command, Wait)
            and pools[f"fp:{actor.actor_id}"].current < self.rules.fatigue_cost
        ):
            return result("rejected", "resource.fatigue")
        if not isinstance(command, Wait) and actor.conditions:
            return result("rejected", "actor.condition")
        if actor.available_at > state.resources.game_time and not isinstance(command, Wait):
            return result("rejected", "actor.not_ready")
        entities = {e.id: e for e in state.world.entities}
        entity = entities[actor.actor_id]
        known = set(actor.aware_of) | {
            f.subject_id for f in state.world.perspective(actor.actor_id).facts
        }
        if entity.location_id is not None:
            known.add(entity.location_id)
        if isinstance(command, Wait):
            return (
                result("feasible", "wait.allowed")
                if command.ticks <= self.rules.maximum_wait
                else result("rejected", "wait.limit")
            )
        if isinstance(command, Move):
            hp = pools[f"hp:{actor.actor_id}"]
            if hp.injury and any(
                p in ("left-leg", "right-leg", "left-foot", "right-foot")
                for p in disabled_locations(
                    hp.injury.lasting_injuries,
                    now=state.resources.game_time,
                    full_hp=hp.current >= hp.maximum,
                )
            ):
                return result("rejected", "move.crippled")
            if self.rules.scenes is not None:
                return result("rejected", "scene.command_required")
            if command.destination_id is None:
                return result("clarification", "move.destination_required")
            destination = entities.get(command.destination_id)
            if command.destination_id not in known or destination is None:
                return result("rejected", "target.unavailable")
            if destination.kind is not EntityKind.LOCATION or not any(
                c.source_id == entity.location_id and c.destination_id == destination.id
                for c in state.world.connections
            ):
                return result("rejected", "move.no_connection")
            return result("feasible", "move.allowed")
        if isinstance(command, UseItem):
            if command.item_id is None:
                return result("clarification", "item.required")
            item = next(
                (
                    i
                    for i in state.resources.items
                    if i.id == command.item_id and i.owner_id == actor.actor_id
                ),
                None,
            )
            if item is None:
                return result("rejected", "item.unavailable")
            if item.container_id is not None or item.equipped:
                return result("rejected", "item.inaccessible")
            if item.quantity < command.quantity:
                return result("rejected", "item.insufficient")
            if any(i.container_id == item.id for i in state.resources.items):
                return result("rejected", "item.container_not_empty")
            if item.definition_id not in self.rules.consumables:
                return result("unsupported", "item.use_unsupported")
            return result("feasible", "item.allowed")
        if command.target_id is None:
            return result("clarification", "target.required")
        target = entities.get(command.target_id)
        if command.target_id not in known or target is None:
            return result("rejected", "target.unavailable")
        if self._location(entity) is None or self._location(entity) != self._location(target):
            return result("rejected", "target.out_of_range")
        if isinstance(command, Attack):
            if command.weapon_id is None:
                return result("clarification", "attack.weapon_required")
            weapon = next(
                (
                    i
                    for i in state.resources.items
                    if i.id == command.weapon_id
                    and i.owner_id == actor.actor_id
                    and i.equipped
                    and i.ready
                ),
                None,
            )
            if weapon is None:
                return result("rejected", "attack.weapon_not_ready")
            return result("unsupported", "combat.not_implemented")
        if isinstance(command, Social) and command.approach != "diplomacy":
            return result("adjudication_required", "social.approach_requires_ruling")
        rule = self.checks.get((command.kind, command.target_id))
        if rule is None:
            return result("unsupported", "check.not_implemented")
        if state.resources.game_time < rule.not_before:
            return result("rejected", "check.not_ready")
        if rule.required_equipment is not None and not any(
            i.owner_id == actor.actor_id and i.definition_id == rule.required_equipment and i.ready
            for i in state.resources.items
        ):
            return result("rejected", "check.equipment_required")
        build, _ = self.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
        )
        if self.reviewer.compiler.skills is not None:
            if rule.definition_id not in {v.target for v in build.sheet.values}:
                return result("rejected", "check.skill_required")
        elif rule.definition_id not in {p.definition_id for p in build.purchases}:
            return result("rejected", "check.skill_required")
        return result("feasible", "check.allowed")

    def _target(
        self, state: PlayState, actor_id: str, build: ValidatedBuild, rule: CheckRule
    ) -> tuple[DerivedValue, tuple[DerivedValue, ...]]:
        values = {v.target: v.value for v in build.sheet.values}
        if self.reviewer.compiler.skills is not None:
            return self._gurps_target(state, actor_id, build, rule)
        definition = self.reviewer.compiler.definitions[rule.definition_id]
        attribute, _ = SKILLS[definition.name]
        attribute_id = f"attribute:{attribute.lower()}"
        effects = self.resources.equipment_effects(state.resources, actor_id)
        evaluator = EffectEvaluator(
            (MechanicalTarget(attribute_id), MechanicalTarget(rule.definition_id, (attribute_id,)))
        )
        attribute_value = evaluator.evaluate(
            attribute_id,
            values[attribute_id],
            effects,
            context={"actor_id": actor_id},
            at=state.resources.game_time,
        )
        # Purchased bonuses are in the compiled sheet; apply only the equipment delta.
        base = values[rule.definition_id] + attribute_value.value - values[attribute_id]
        skill = evaluator.evaluate(
            rule.definition_id,
            base,
            effects,
            context={"actor_id": actor_id},
            at=state.resources.game_time,
        )
        return skill, (attribute_value,)

    def _gurps_target(
        self, state: PlayState, actor_id: str, build: ValidatedBuild, rule: CheckRule
    ) -> tuple[DerivedValue, tuple[DerivedValue, ...]]:
        compiler = self.reviewer.compiler
        assert compiler.skills is not None
        values = {v.target: v.value for v in build.sheet.values}
        equipment = self.resources.equipment_effects(state.resources, actor_id)
        selected = {p.definition_id for p in build.purchases}
        skill_effects = tuple(e for key, e in compiler.effects if key in selected) + equipment
        evaluator = EffectEvaluator(
            tuple(MechanicalTarget(k) for k in values | compiler.skills.specs)
        )
        context = {"actor_id": actor_id}
        attributes = {
            key: evaluator.evaluate(
                key, value, equipment, context=context, at=state.resources.game_time
            )
            for key, value in values.items()
            if key.startswith(("attribute:", "secondary:"))
        }
        derived: dict[str, DerivedValue] = {}

        def adjust(key: str, base: int) -> int:
            result = evaluator.evaluate(
                key, Decimal(base), skill_effects, context=context, at=state.resources.game_time
            )
            if not result.value.is_finite() or result.value != result.value.to_integral_value():
                raise ValidationError("Skill effects must produce whole-number levels")
            derived[key] = result
            return int(result.value)

        compiler.skills.compile(
            {
                p.definition_id: p.amount
                for p in build.purchases
                if p.definition_id in compiler.skills.specs
            },
            {key: value.value for key, value in attributes.items()},
            adjust,
        )
        return derived[rule.definition_id], tuple(attributes.values())

    def resolve(
        self,
        state: PlayState,
        command: TypedAction,
        *,
        rng: RandomSource = NO_RANDOM,
        ruling_id: str | None = None,
        advance_time: bool = True,
    ) -> tuple[PlayState, list[EngineEvent]]:
        updated, result = self._resolve_action(
            state, command, rng=rng, ruling_id=ruling_id, advance_time=advance_time
        )
        events = play_events(state, updated, command.actor_id)
        # Rejected/question resolutions still produce a typed result, but are not committed.
        events = [e for e in events if not isinstance(e, ActionResolved)]
        events.append(
            ActionResolved(audience=ActorAudience(actor_ids=(command.actor_id,)), result=result)
        )
        return updated, events

    def _resolve_action(
        self,
        state: PlayState,
        command: TypedAction,
        *,
        rng: RandomSource = NO_RANDOM,
        ruling_id: str | None = None,
        advance_time: bool = True,
    ) -> tuple[PlayState, ActionResult]:
        from wayfarer.engine.simulation.object_repairs import tasks

        if not isinstance(command, (Wait, Question)) and any(
            t.status == "pending" and t.actor_id == command.actor_id for t in tasks(state.resources)
        ):
            raise ConflictError("Finish or cancel the repair attempt before acting")
        ruling = None
        extra_modifiers: tuple[Modifier, ...] = ()
        if ruling_id is not None:
            # Consent is loaded from canonical state, never accepted as an input
            # approval object. The ordinary engine gates still apply below.
            self.validate(state)
            ruling = next((r for r in state.rulings if r.id == ruling_id), None)
            if (
                ruling is None
                or ruling.current_status(state.revision, state.resources.game_time) != "approved"
            ):
                raise ConflictError("Ruling is not approved at the current revision")
            original = ACTION_ADAPTER.validate_json(ruling.original_action_json)
            if not isinstance(original, Social):
                raise ValidationError("Unsupported ruling action")
            expected = original.model_copy(
                update={
                    "approach": "diplomacy",
                    "id": command.id,
                    "expected_revision": state.revision,
                }
            )
            if command != expected:
                raise ValidationError("Command does not match the approved ruling")
            selected = next(a for a in ruling.alternatives if a.id == ruling.selected_id)
            extra_modifiers = (
                Modifier(
                    selected.modifier,
                    f"ruling:{ruling.id}:{selected.id}",
                    ruling.policy_digest,
                    ruling.configuration_digest,
                ),
            )
        feasible = self.assess(state, command)
        if feasible.status != "feasible":
            return state, feasible
        world, resources = state.world, state.resources
        if not isinstance(command, Wait):
            from wayfarer.engine.rules.recovery_types import interrupt_tasks
            from wayfarer.engine.simulation.abilities import interrupt_concentration

            resources = interrupt_concentration(resources, command.actor_id, command.id)
            resources = resources.model_copy(
                update={
                    "recovery_tasks": interrupt_tasks(
                        resources.recovery_tasks, frozenset({command.actor_id}), resources.game_time
                    )
                }
            )
        if not isinstance(command, Wait) and self.rules.fatigue_cost:
            if any(
                p.id == f"fp:{command.actor_id}" and p.fatigue is not None for p in resources.pools
            ):
                raise ValidationError("Profile fatigue costs require the GURPS exertion adapter")
            resources = resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": p.current - self.rules.fatigue_cost})
                        if p.id == f"fp:{command.actor_id}"
                        else p
                        for p in resources.pools
                    )
                }
            )
        duration = 0
        trace: CheckTrace | None = None
        derived: DerivedValue | None = None
        dependencies: tuple[DerivedValue, ...] = ()
        revealed: tuple[str, ...] = ()
        if isinstance(command, Move):
            world = replace(
                world,
                entities=tuple(
                    replace(e, location_id=command.destination_id)
                    if e.id == command.actor_id
                    else e
                    for e in world.entities
                ),
            )
            duration = self.rules.movement_ticks
        elif isinstance(command, UseItem):
            assert command.item_id is not None
            resources = self.resources.apply(
                resources,
                Consume(
                    id=f"{command.id}:consume",
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                    item_id=command.item_id,
                    quantity=command.quantity,
                ),
            )
            duration = self.rules.item_ticks
        elif isinstance(command, Wait):
            duration = command.ticks
        elif isinstance(command, (Inspect, Social)):
            assert command.target_id is not None
            rule = self.checks[(command.kind, command.target_id)]
            actor = next(a for a in state.actors if a.actor_id == command.actor_id)
            build, _ = self.reviewer.activate(
                actor.proposal,
                actor.approval,
                campaign_id=state.campaign_id,
                actor_id=actor.actor_id,
            )
            derived, dependencies = self._target(state, actor.actor_id, build, rule)
            if not derived.value.is_finite() or derived.value != derived.value.to_integral_value():
                raise ValidationError("Check target must be a finite integer")
            from wayfarer.engine.simulation.spell_effects import lighting_penalty

            darkness = lighting_penalty(state, rule.target_id, rule.darkness_penalty)
            trace = success_check(
                int(derived.value),
                (Modifier(rule.modifier, rule.id, rule.definition_id, rule.package_version),)
                + (
                    (
                        Modifier(
                            darkness,
                            rule.id + ":darkness",
                            rule.definition_id,
                            rule.package_version,
                        ),
                    )
                    if darkness
                    else ()
                )
                + extra_modifiers
                + definition_modifiers(
                    state.resources,
                    actor.actor_id,
                    rule.definition_id,
                    self.reviewer.compiler.definitions,
                ),
                rng=rng,
                rules_package=rule.package_id,
                rules_version=rule.package_version,
            )
            if trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS):
                revealed = rule.reveal_fact_ids
                for fact_id in revealed:
                    world = world.learn(actor.actor_id, fact_id)
            duration = rule.duration
        else:
            raise ValidationError("No implemented resolver")
        if advance_time:
            resources = self.resources.apply(
                resources,
                Advance(
                    id=f"{command.id}:time",
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                    to=resources.game_time + duration,
                ),
                system=True,
                rng=rng,
            )
        # Subcommands execute atomically within one campaign command/revision.
        resources = resources.model_copy(update={"revision": state.revision + 1})
        result = ActionResult(
            status="committed",
            revision=state.revision + 1,
            code=f"{command.kind}.resolved",
            command_id=command.id,
            check=trace,
            derived=derived,
            dependencies=dependencies,
            revealed_fact_ids=revealed,
            rules_digest=self.digest,
            ruling_id=ruling_id,
        )
        journal = state.journal
        scene_events = state.scene_events
        if revealed and self.rules.scenes is not None:
            scene_cursor = next(
                value for value in state.actor_scenes if value.actor_id == command.actor_id
            )
            discoveries = {
                value.fact_id: value
                for value in self.rules.scenes.discoveries
                if value.scene_id == scene_cursor.scene_id
                and value.mode == "check"
                and value.target_id == getattr(command, "target_id", None)
            }
            additions = tuple(
                JournalEntry(
                    id=f"{command.id}:{discoveries[fact_id].id}",
                    actor_id=command.actor_id,
                    scene_id=scene_cursor.scene_id,
                    fact_id=fact_id,
                    at=resources.game_time,
                )
                for fact_id in revealed
                if fact_id in discoveries
                and not any(
                    entry.actor_id == command.actor_id and entry.fact_id == fact_id
                    for entry in state.journal
                )
            )
            journal += additions
            scene_events += tuple(
                SceneEvent(
                    id=f"{entry.id}:event",
                    actor_id=entry.actor_id,
                    scene_id=entry.scene_id,
                    kind="discovered",
                    fact_id=entry.fact_id,
                    at=entry.at,
                )
                for entry in additions
            )
        updated = state.model_copy(
            update={
                "world": world,
                "resources": resources,
                "revision": state.revision + 1,
                "last_result": result,
                "journal": journal,
                "scene_events": scene_events,
                "rulings": expire_rulings(
                    tuple(
                        r.model_copy(
                            update={"status": "executed", "executed_revision": state.revision + 1}
                        )
                        if ruling is not None and r.id == ruling.id
                        else r
                        for r in state.rulings
                    ),
                    state.revision + 1,
                    resources.game_time,
                ),
            }
        )
        if advance_time and updated.party.groups:
            updated = updated.model_copy(
                update={
                    "party": updated.party.model_copy(
                        update={
                            "groups": tuple(
                                g.model_copy(update={"ready_through": resources.game_time})
                                for g in updated.party.groups
                            )
                        }
                    )
                }
            )
        self.validate(updated)
        return updated, result
