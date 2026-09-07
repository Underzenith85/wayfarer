"""Typed proposals, feasibility, deterministic resolution and canonical play state."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import replace
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.character.compiler import ValidatedBuild, pool_limits
from wayfarer.character.power import Approval, CharacterProposal, PowerReviewer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import SKILLS, DefinitionKind, ImplementationStatus
from wayfarer.rules.checks import CheckTrace, Modifier, Outcome, RandomSource, success_check
from wayfarer.rules.effects import DerivedValue, EffectEvaluator, MechanicalTarget
from wayfarer.rules.hazard_types import require_hazards_settled
from wayfarer.rules.location_types import Hand, HumanBody, disabled_locations
from wayfarer.simulation.ability_types import AbilityRules
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.adjudication import Ruling, RulingPolicy, expire_rulings
from wayfarer.simulation.advancement import AdvancementEntry, MigrationEntry
from wayfarer.simulation.combat import CombatEngine, CombatResult, CombatRules, Encounter
from wayfarer.simulation.director import AuthorDraft, DirectorTurn
from wayfarer.simulation.noncombat import NoncombatEncounter, NoncombatRules
from wayfarer.simulation.npcs import NPCRules, NPCState
from wayfarer.simulation.objectives import ObjectiveRules, ObjectiveState
from wayfarer.simulation.party import PartyRules, PartyState
from wayfarer.simulation.party import validate as validate_party
from wayfarer.simulation.recovery import RecoveryRules, RecoveryState
from wayfarer.simulation.resources import Advance, Consume, Record, ResourceEngine, ResourceState
from wayfarer.simulation.scenes import ActorScene, JournalEntry, SceneEvent, SceneRules
from wayfarer.simulation.spell_bindings import SpellRules
from wayfarer.world import Entity, EntityKind, World

Id = Annotated[str, Field(min_length=1, max_length=100)]


class ActionCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    hypothetical: bool = False


class Move(ActionCommand):
    kind: Literal["move"] = "move"
    destination_id: Id | None = None


class Inspect(ActionCommand):
    kind: Literal["inspect"] = "inspect"
    target_id: Id | None = None


class Attack(ActionCommand):
    kind: Literal["attack"] = "attack"
    target_id: Id | None = None
    weapon_id: Id | None = None


class UseItem(ActionCommand):
    kind: Literal["use_item"] = "use_item"
    item_id: Id | None = None
    quantity: int = Field(default=1, ge=1, le=1000000)


class Social(ActionCommand):
    kind: Literal["social"] = "social"
    target_id: Id | None = None
    approach: Literal["diplomacy", "intimidation", "deception"] = "diplomacy"


class Wait(ActionCommand):
    kind: Literal["wait"] = "wait"
    ticks: int = Field(ge=1, le=10000)


class Question(ActionCommand):
    kind: Literal["question"] = "question"
    text: str = Field(min_length=1, max_length=2000)


TypedAction = Annotated[
    Move | Inspect | Attack | UseItem | Social | Wait | Question, Field(discriminator="kind")
]
ACTION_ADAPTER: TypeAdapter[TypedAction] = TypeAdapter(TypedAction)


class ActorSetup(Record):
    actor_id: Id
    proposal: CharacterProposal
    aware_of: tuple[str, ...] = ()
    conditions: tuple[Literal["unconscious", "stunned", "restrained"], ...] = ()
    available_at: int = Field(default=0, ge=0)
    body: HumanBody | None = None
    held_item_hands: tuple[tuple[str, Hand], ...] = ()


class PlayActor(ActorSetup):
    approval: Approval | None = None


class CheckRule(Record):
    """Trusted scenario check with catalog skill and modifier provenance."""

    id: Id
    action: Literal["inspect", "social"]
    target_id: Id
    definition_id: Id
    package_id: str
    package_version: str
    duration: int = Field(default=1, ge=1, le=10000)
    modifier: int = Field(default=0, ge=-20, le=20)
    reveal_fact_ids: tuple[str, ...] = ()
    required_equipment: str | None = None
    not_before: int = Field(default=0, ge=0)
    darkness_penalty: int = Field(default=0, ge=-9, le=0, exclude_if=lambda value: value == 0)


class ActionRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_ticks: int = Field(default=1, ge=1, le=10000)
    item_ticks: int = Field(default=1, ge=1, le=10000)
    maximum_wait: int = Field(default=100, ge=1, le=10000)
    fatigue_cost: int = Field(default=0, ge=0, le=100)
    checks: tuple[CheckRule, ...] = ()
    consumables: tuple[str, ...] = ()
    adjudication: RulingPolicy | None = None
    combat: CombatRules | None = None
    # Kept out of the legacy ActionRules encoding so disabled scenes preserve
    # existing campaign digests; enabled scene rules are appended explicitly.
    scenes: SceneRules | None = Field(default=None, exclude=True)
    objectives: ObjectiveRules | None = Field(default=None, exclude=True)
    noncombat: NoncombatRules | None = Field(default=None, exclude=True)
    party: PartyRules | None = Field(default=None, exclude=True)
    npcs: NPCRules | None = Field(default=None, exclude=True)
    recovery: RecoveryRules | None = Field(default=None, exclude=True)
    abilities: AbilityRules | None = Field(default=None, exclude=True)
    spells: SpellRules | None = Field(default=None, exclude=True)


class ActionResult(Record):
    status: Literal[
        "feasible",
        "clarification",
        "question",
        "rejected",
        "unsupported",
        "adjudication_required",
        "committed",
    ]
    revision: int
    code: str
    command_id: str
    check: CheckTrace | None = None
    derived: DerivedValue | None = None
    dependencies: tuple[DerivedValue, ...] = ()
    revealed_fact_ids: tuple[str, ...] = ()
    rules_digest: str = ""
    ruling_id: str | None = None


class PlayState(Record):
    campaign_id: str
    lifecycle: Literal["active", "paused", "completed", "archived"] = "active"
    revision: int = Field(default=0, ge=0)
    configuration_digest: str
    world: World
    resources: ResourceState
    actors: tuple[PlayActor, ...]
    approvals: tuple[Approval, ...] = ()
    last_result: ActionResult | None = None
    rulings: tuple[Ruling, ...] = ()
    encounters: tuple[Encounter, ...] = ()
    last_combat_result: CombatResult | None = None
    advancement: tuple[AdvancementEntry, ...] = ()
    migrations: tuple[MigrationEntry, ...] = ()
    members: tuple[CampaignMember, ...] = ()
    actor_scenes: tuple[ActorScene, ...] = ()
    scene_events: tuple[SceneEvent, ...] = ()
    journal: tuple[JournalEntry, ...] = ()
    fired_scene_triggers: tuple[str, ...] = ()
    objectives: ObjectiveState = ObjectiveState()
    noncombat: tuple[NoncombatEncounter, ...] = ()
    party: PartyState = PartyState()
    npcs: NPCState = NPCState()
    recovery: RecoveryState = RecoveryState()
    director: tuple[DirectorTurn, ...] = ()
    drafts: tuple[AuthorDraft, ...] = ()


class ActionEngine:
    def __init__(
        self, reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
    ) -> None:
        rules = ActionRules.model_validate(rules)
        if reviewer.compiler.rules != resources.rules:
            raise ValidationError("Character and resource rules differ")
        self.reviewer, self.resources, self.rules = reviewer, resources, rules
        self.checks = {(r.action, r.target_id): r for r in rules.checks}
        if len(self.checks) != len(rules.checks) or len({r.id for r in rules.checks}) != len(
            rules.checks
        ):
            raise ValidationError("Ambiguous action check rules")
        definitions = reviewer.compiler.definitions
        if rules.spells is not None:
            from wayfarer.rules.gurps_magic import validate_definitions

            validate_definitions(reviewer.compiler.statistics_profile, definitions)
            if reviewer.compiler.statistics_profile != rules.spells.profile_id:
                raise ValidationError("Spell rules require the exact compiled profile")
            from wayfarer.rules.spell_catalog import projectile_definition

            if (
                any(c.spell_id == "fireball" for c in rules.spells.channels)
                and definitions.get("skill:innate-attack-projectile") != projectile_definition()
            ):
                raise ValidationError("Fireball requires the pinned projectile skill")
            if any("spell:" + c.spell_id not in definitions for c in rules.spells.channels):
                raise ValidationError("Spell channels require pinned training definitions")
        if rules.abilities is not None:
            from wayfarer.rules.abilities import definition as ability_definition
            from wayfarer.rules.abilities import metadata, validate_binding
            from wayfarer.rules.traits import TraitOptions

            if reviewer.compiler.statistics_profile != rules.abilities.profile_id:
                raise ValidationError("Ability rules require the exact compiled profile")
            for ability in rules.abilities.abilities:
                definition = definitions.get(ability.definition_id)
                if definition is None or definition.trait_rules != metadata(ability):
                    raise ValidationError("Ability runtime metadata differs from pinned catalog")
                expected = ability_definition(ability)
                if (
                    definition.point_cost != expected.point_cost
                    or definition.status != expected.status
                    or "supernatural" not in definition.hooks
                ):
                    raise ValidationError(
                        "Ability cost or implementation status differs from runtime"
                    )
                validate_binding(ability, 1, TraitOptions(modifiers=ability.modifiers))
        for rule in rules.checks:
            definition = definitions.get(rule.definition_id)
            if (
                definition is None
                or definition.kind is not DefinitionKind.SKILL
                or definition.status is not ImplementationStatus.IMPLEMENTED
            ):
                raise ValidationError("Action check requires an implemented catalog skill")
            if (rule.package_id, rule.package_version) != reviewer.compiler.definition_packages[
                rule.definition_id
            ]:
                raise ValidationError("Check provenance is not pinned")
            if rule.action == "social" and rule.definition_id != "skill:diplomacy":
                raise ValidationError("Only diplomacy social checks are implemented")
            if (
                rule.required_equipment is not None
                and rule.required_equipment not in resources.specs
            ):
                raise ValidationError("Unknown required equipment")
        if any(key not in resources.specs for key in rules.consumables):
            raise ValidationError("Unknown consumable definition")
        if rules.adjudication is not None:
            by_id = {r.id: r for r in rules.checks}
            for alternative in rules.adjudication.alternatives:
                check = by_id.get(alternative.check_rule_id)
                if check is None or check.action != "social":
                    raise ValidationError("Ruling requires an existing social check")
                if not -20 <= check.modifier + alternative.modifier <= 20:
                    raise ValidationError("Combined ruling modifier exceeds engine bounds")
        self.combat = CombatEngine(rules.combat, resources) if rules.combat is not None else None
        if rules.combat is not None and rules.combat.gurps_equipment is not None:
            equipment = rules.combat.gurps_equipment
            if reviewer.compiler.statistics_profile != equipment.profile_id:
                raise ValidationError("Combat and compiled statistics profiles must match")
            if rules.combat.attacks or rules.combat.protection:
                raise ValidationError("GURPS combat cannot use prototype attack profiles")
            for entry in equipment.entries:
                definition = reviewer.compiler.definitions.get(entry.definition_id)
                if (
                    definition is None
                    or definition.kind is not DefinitionKind.EQUIPMENT
                    or definition.source_id != entry.provenance.source_id
                ):
                    raise ValidationError(
                        "Combat equipment source does not match pinned definitions"
                    )
                referenced = tuple(m.skill_id for m in entry.modes) + (
                    (entry.shield.skill_id,) if entry.shield else ()
                )
                if any(
                    key not in reviewer.compiler.definitions
                    or reviewer.compiler.definitions[key].kind is not DefinitionKind.SKILL
                    for key in referenced
                ):
                    raise ValidationError("Combat equipment requires pinned weapon/shield skills")
            if resources.specs != {e.definition_id: e.inventory_spec() for e in equipment.entries}:
                raise ValidationError("Combat requires exact profile inventory specifications")
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
        self.digest = hashlib.sha256(payload.encode()).hexdigest()

    def validate(self, state: PlayState) -> None:
        if state.resources.transports:
            raise ValidationError("Live transport encounters require the #120 integration")
        if state.configuration_digest != self.digest:
            raise ValidationError("Play configuration changed; explicit migration required")
        if state.revision != state.resources.revision:
            raise ValidationError("Play and resource revisions diverged")
        if self.rules.spells:
            entities = {e.id: e for e in state.world.entities}
            for spell_channel in self.rules.spells.channels:
                if (
                    spell_channel.actor_id not in entities
                    or spell_channel.target_id not in entities
                    or spell_channel.location_id not in entities
                    or entities[spell_channel.location_id].kind is not EntityKind.LOCATION
                ):
                    raise ValidationError("Invalid spell spell_channel entity references")
        if self.rules.abilities:
            ability_entities = {e.id for e in state.world.entities}
            facts = {f.id for f in state.world.facts}
            for channel in self.rules.abilities.channels:
                if (
                    not {channel.actor_id, channel.target_id, channel.location_id}
                    <= ability_entities
                ):
                    raise ValidationError("Invalid ability channel entity references")
                if (
                    not set(
                        (
                            *channel.presence_fact_ids,
                            *channel.detection_fact_ids,
                            *channel.precise_fact_ids,
                            *channel.analysis_fact_ids,
                            *channel.thought_fact_ids,
                        )
                    )
                    <= facts
                ):
                    raise ValidationError("Invalid ability channel fact references")
        if self.rules.combat:
            consequence_actors = {a.actor_id for a in state.actors}
            facts = {f.id for f in state.world.facts}
            fields = {b.id for b in self.rules.combat.battlefields}
            consequences = self.rules.combat.consequences
            if len({c.id for c in consequences}) != len(consequences):
                raise ValidationError("Duplicate combat consequence")
            if any(
                c.battlefield_id not in fields
                or c.defeated_actor_id not in consequence_actors
                or not set(c.recipient_actor_ids) <= consequence_actors
                or not set(c.fact_ids) <= facts
                for c in consequences
            ):
                raise ValidationError("Invalid combat consequence references")
        if self.rules.objectives is not None:
            self.rules.objectives.validate_state(state, frozenset(self.resources.specs))
        if self.rules.noncombat is not None:
            scenes = {s.id for s in self.rules.scenes.scenes} if self.rules.scenes else set()
            facts = {f.id for f in state.world.facts}
            for encounter_rule in self.rules.noncombat.encounters:
                if encounter_rule.scene_id not in scenes:
                    raise ValidationError("Noncombat encounter requires a scene")
                for approach in encounter_rule.approaches:
                    if approach.check_rule_id not in {c.id for c in self.rules.checks}:
                        raise ValidationError("Unknown noncombat check")
                    if not set((*approach.success_fact_ids, *approach.failure_fact_ids)) <= facts:
                        raise ValidationError("Unknown approach consequence")
                if (
                    not set(
                        (
                            *encounter_rule.completion_fact_ids,
                            *encounter_rule.defeat_fact_ids,
                            *encounter_rule.withdrawal_fact_ids,
                        )
                    )
                    <= facts
                ):
                    raise ValidationError("Unknown encounter consequence")
            if len({e.id for e in state.noncombat}) != len(state.noncombat):
                raise ValidationError("Duplicate noncombat instance")
            for encounter_state in state.noncombat:
                if encounter_state.rule_id not in {
                    r.id for r in self.rules.noncombat.encounters
                } or encounter_state.actor_id not in {a.actor_id for a in state.actors}:
                    raise ValidationError("Invalid noncombat instance")
        elif state.noncombat:
            raise ValidationError("Noncombat state requires rules")
        if self.rules.party is not None:
            effects = self.rules.party.effects
            if len({e.id for e in effects}) != len(effects):
                raise ValidationError("Duplicate cross-scene effect")
            scenes = {s.id for s in self.rules.scenes.scenes} if self.rules.scenes else set()
            party_actors = {a.actor_id for a in state.actors}
            if any(
                e.source_scene_id not in scenes
                or e.fact_id not in {f.id for f in state.world.facts}
                or not set(e.recipient_actor_ids) <= party_actors
                for e in effects
            ):
                raise ValidationError("Invalid cross-scene effect references")
        from wayfarer.simulation.lifecycle import validate_lifecycle

        validate_lifecycle(state, self.rules)
        validate_party(state)
        self.validate_rulings(state)
        state.world.validate()
        self.resources.validate(state.resources)
        if self.rules.scenes is None:
            if (
                state.actor_scenes
                or state.scene_events
                or state.journal
                or state.fired_scene_triggers
            ):
                raise ValidationError("Campaign has scene state without scene rules")
        else:
            self.rules.scenes.validate_world(state.world)
            scenes = {scene.id for scene in self.rules.scenes.scenes}
            if len({value.actor_id for value in state.actor_scenes}) != len(state.actor_scenes):
                raise ValidationError("Duplicate actor scene cursor")
            if {value.actor_id for value in state.actor_scenes} != {
                a.actor_id for a in state.actors
            }:
                raise ValidationError("Every actor requires one scene cursor")
            if any(value.scene_id not in scenes for value in state.actor_scenes):
                raise ValidationError("Unknown actor scene")
            if len({value.id for value in state.scene_events}) != len(state.scene_events):
                raise ValidationError("Duplicate scene event")
            if any(
                value.actor_id not in {actor.actor_id for actor in state.actors}
                or value.scene_id not in scenes
                or value.revision > state.revision
                for value in state.scene_events
            ):
                raise ValidationError("Invalid scene event")
            if len({value.id for value in state.journal}) != len(state.journal):
                raise ValidationError("Duplicate journal entry")
            facts = {fact.id for fact in state.world.facts}
            if any(
                value.actor_id not in {actor.actor_id for actor in state.actors}
                or value.scene_id not in scenes
                or value.fact_id not in facts
                or (value.actor_id, value.fact_id) not in state.world.knowledge
                for value in state.journal
            ):
                raise ValidationError("Invalid perspective journal entry")
            trigger_ids = {value.id for value in self.rules.scenes.triggers}
            if (
                len(set(state.fired_scene_triggers)) != len(state.fired_scene_triggers)
                or not set(state.fired_scene_triggers) <= trigger_ids
            ):
                raise ValidationError("Invalid fired scene trigger")
        if len({entry.id for entry in state.advancement}) != len(state.advancement):
            raise ValidationError("Duplicate advancement ledger ID")
        if len({entry.id for entry in state.migrations}) != len(state.migrations):
            raise ValidationError("Duplicate migration ledger ID")
        if len({member.principal_id for member in state.members}) != len(state.members):
            raise ValidationError("Duplicate campaign member")
        actor_ids = {actor.actor_id for actor in state.actors}
        if any(
            len(set(member.actor_ids)) != len(member.actor_ids)
            or not set(member.actor_ids) <= actor_ids
            or (member.role != "player" and member.actor_ids)
            for member in state.members
        ):
            raise ValidationError("Invalid campaign actor control")
        if any(entry.revision > state.revision for entry in state.advancement) or any(
            entry.revision > state.revision for entry in state.migrations
        ):
            raise ValidationError("Ledger entry is ahead of campaign state")
        if len({e.id for e in state.encounters}) != len(state.encounters):
            raise ValidationError("Duplicate encounter ID")
        active_actors: set[str] = set()
        for encounter in state.encounters:
            if self.combat is None:
                raise ValidationError("Campaign has encounters without combat rules")
            self.combat.validate(
                encounter,
                state.world,
                state.resources,
                frozenset(actor.actor_id for actor in state.actors),
            )
            if encounter.status == "active":
                participants = {p.actor_id for p in encounter.participants}
                if active_actors & participants:
                    raise ValidationError("Actor participates in multiple active encounters")
                active_actors |= participants
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
        if any(
            participant.initiative != initiatives[participant.actor_id]
            for encounter in state.encounters
            for participant in encounter.participants
        ):
            raise ValidationError("Combat initiative disagrees with compiled character")
        for entity in state.world.entities:
            if (
                entity.location_id is not None
                and entities[entity.location_id].kind is not EntityKind.LOCATION
            ):
                raise ValidationError("Entity location must reference a location")
        facts = {f.id for f in state.world.facts}
        for rule in self.rules.checks:
            if rule.target_id not in entities or not set(rule.reveal_fact_ids) <= facts:
                raise ValidationError("Invalid scenario check references")
            if rule.action == "social" and entities[rule.target_id].kind is not EntityKind.ACTOR:
                raise ValidationError("Social target must be an actor")

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
        if any(
            encounter.status == "active" and command.actor_id in encounter.turn_order
            for encounter in state.encounters
        ):
            return result("rejected", "combat.command_required")
        if isinstance(command, Question) or command.hypothetical:
            return result("question", "action.no_effect")
        from wayfarer.simulation.spell_effects import dazed

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
        rng: RandomSource = secrets,
        ruling_id: str | None = None,
        advance_time: bool = True,
    ) -> tuple[PlayState, ActionResult]:
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
            from wayfarer.rules.recovery_types import interrupt_tasks
            from wayfarer.simulation.abilities import interrupt_concentration

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
            from wayfarer.simulation.spell_effects import illuminated

            darkness = 0 if illuminated(state, rule.target_id) else rule.darkness_penalty
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
                + extra_modifiers,
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
