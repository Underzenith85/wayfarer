"""The Last Lantern: a small authored adventure, never dependent on test fixtures."""

from dataclasses import replace
from functools import cache
from importlib.resources import files
from typing import TypedDict

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.simulation.actions import ActionEngine, ActionRules, ActorSetup, CheckRule
from wayfarer.simulation.combat import AttackProfile, Battlefield, CombatConsequence, CombatRules
from wayfarer.simulation.noncombat import Approach, NoncombatRule, NoncombatRules
from wayfarer.simulation.npcs import NPCAction, NPCPlan, NPCRules
from wayfarer.simulation.objectives import Objective, ObjectiveRules, Predicate, Reward
from wayfarer.simulation.party import PartyRules
from wayfarer.simulation.recovery import RecoveryOption, RecoveryRules, SetbackRule
from wayfarer.simulation.resources import EquipmentSpec, Item, Owner, ResourceEngine, ResourceState
from wayfarer.simulation.scenes import Discovery, Scene, SceneExit, SceneRules
from wayfarer.simulation.studio import GenerationBrief, ScenarioGraph
from wayfarer.world import Commitment, CommitmentKind, Connection, Entity, EntityKind, Fact, World


def world() -> World:
    return World(
        entities=(
            Entity("harbor", EntityKind.LOCATION, "Lantern Harbor"),
            Entity("warehouse", EntityKind.LOCATION, "Old Customs House"),
            Entity("a", EntityKind.ACTOR, "Mira", location_id="harbor"),
            Entity("b", EntityKind.ACTOR, "Iven", location_id="harbor"),
            Entity("warden", EntityKind.ACTOR, "Warden Sable", location_id="harbor"),
            Entity("manifest", EntityKind.OBJECT, "Sealed shipping manifest", location_id="harbor"),
            Entity(
                "records", EntityKind.OBJECT, "Duplicate customs records", location_id="warehouse"
            ),
            Entity("ferrymen", EntityKind.FACTION, "The Ferrymen"),
            Entity("escrow", EntityKind.ACTOR, "Ferrymen reward chest", location_id="harbor"),
        ),
        connections=(
            Connection("harbor", "warehouse", "customs path"),
            Connection("warehouse", "harbor", "quayside path"),
        ),
        facts=(
            Fact(
                "manifest-found",
                "manifest",
                "cargo",
                "The last ferry carries stolen relief supplies.",
            ),
            Fact(
                "passage-secured",
                "ferrymen",
                "passage",
                "The party secured passage for the relief supplies.",
            ),
            Fact("capture-signal", "a", "detention", "Mira is held in the harbor guardhouse."),
            Fact(
                "inside-route",
                "manifest",
                "route",
                "A loose shutter opens onto the prisoners' yard.",
            ),
            Fact("warden-secret", "warden", "motive", "Sable secretly owes the Ferrymen a debt."),
            Fact(
                "opening",
                "harbor",
                "deadline",
                "The last ferry departs at shared tick 12. Find the manifest and secure passage.",
            ),
        ),
        # The warden's private knowledge must never become player knowledge by reunion.
        knowledge=(("warden", "warden-secret"),),
        commitments=(
            Commitment(
                "ferrymen-debt",
                CommitmentKind.DEBT,
                "a",
                "ferrymen",
                "Return the Ferrymen's favor after this voyage.",
                reveal_fact_id="passage-secured",
            ),
        ),
    )


def engine() -> ActionEngine:
    package = replace(
        PROTOTYPE_PACKAGE,
        id="wayfarer-reference-1",
        definitions=PROTOTYPE_PACKAGE.definitions
        + tuple(
            RuleDefinition(
                key,
                DefinitionKind.EQUIPMENT,
                name,
                PROTOTYPE_SOURCE.id,
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for key, name in (("lantern-blade", "Training cutlass"), ("ration", "Travel ration"))
        ),
    )
    policy = replace(DEFAULT_POLICY, allowed_equipment=frozenset({"lantern-blade", "ration"}))
    rules = replace(
        DEFAULT_RULES, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    catalog = RulesCatalog((package,))
    reviewer = PowerReviewer(
        CharacterCompiler(catalog, rules, policy),
        PowerPolicy(id="reference-power", version=1, automatic_approval=True),
        frozenset({"gm"}),
    )
    resources = ResourceEngine(
        world(),
        catalog,
        rules,
        policy,
        (
            EquipmentSpec(
                definition_id="lantern-blade", unit_weight=2, stackable=False, slot="hand"
            ),
            EquipmentSpec(definition_id="ration", unit_weight=1),
        ),
    )
    checks = (
        CheckRule(
            id="read-manifest",
            action="inspect",
            target_id="manifest",
            definition_id="skill:observation",
            package_id=package.id,
            package_version=package.version,
            duration=1,
            reveal_fact_ids=("manifest-found",),
        ),
        CheckRule(
            id="convince-warden",
            action="social",
            target_id="warden",
            definition_id="skill:diplomacy",
            package_id=package.id,
            package_version=package.version,
            duration=1,
        ),
        CheckRule(
            id="slip-lock",
            action="inspect",
            target_id="warden",
            definition_id="skill:stealth",
            package_id=package.id,
            package_version=package.version,
            duration=1,
        ),
    )
    combat = CombatRules(
        id="harbor-combat",
        version=1,
        battlefields=(Battlefield(id="guardhouse-yard", location_id="harbor", width=6, height=6),),
        attacks=(AttackProfile(definition_id="lantern-blade", damage_bonus=2, stun_ticks=0),),
    )
    return ActionEngine(
        reviewer,
        resources,
        ActionRules(id="lantern-actions", version=1, checks=checks, combat=combat),
    )


def character(actor_id: str, name: str) -> ActorSetup:
    return ActorSetup(
        actor_id=actor_id,
        proposal=CharacterProposal(
            draft=CharacterDraft(
                name=name,
                purchases=tuple(
                    Purchase(definition_id="attribute:" + key, amount=10)
                    for key in ("st", "dx", "iq", "ht")
                )
                + tuple(
                    Purchase(definition_id="skill:" + key, amount=4)
                    for key in ("observation", "diplomacy", "stealth")
                ),
            )
        ),
        aware_of=("a", "b", "warden", "manifest", "records", "harbor", "warehouse"),
    )


class RecoveryScope(TypedDict):
    scene_id: str
    actor_ids: tuple[str, ...]
    target_actor_ids: tuple[str, ...]


def build_adventure(*, sequel: bool = False) -> ScenarioGraph:
    base = engine()
    identity = "last-lantern-2" if sequel else "last-lantern-1"
    common: RecoveryScope = {
        "scene_id": "harbor-scene",
        "actor_ids": ("a", "b"),
        "target_actor_ids": ("a", "b"),
    }
    recovery = RecoveryRules(
        id="lantern-recovery",
        version=1,
        setbacks=(
            SetbackRule(
                id="harbor-capture",
                kind="capture",
                actor_ids=("a",),
                captor_id="warden",
                custody_owner_id="warden",
                restraints=("rope",),
                consequence_fact_ids=("capture-signal",),
            ),
        ),
        options=(
            RecoveryOption(
                id="study-cell",
                kind="observe",
                success_fact_ids=("capture-signal", "manifest-found", "inside-route"),
                **common,
            ),
            RecoveryOption(
                id="signal-outside",
                kind="communicate",
                actor_ids=("a",),
                target_actor_ids=("a",),
                scene_id="harbor-scene",
                recipient_actor_ids=("b",),
                communication_fact_ids=("capture-signal",),
            ),
            RecoveryOption(
                id="loosen-bars", kind="assist", success_fact_ids=("inside-route",), **common
            ),
            RecoveryOption(id="slip-bonds", kind="escape", check_rule_id="slip-lock", **common),
            RecoveryOption(
                id="negotiate-release",
                kind="rescue",
                check_rule_id="convince-warden",
                required_fact_ids=("capture-signal",),
                **common,
            ),
            RecoveryOption(
                id="quiet-rescue",
                kind="rescue",
                check_rule_id="slip-lock",
                required_fact_ids=("capture-signal",),
                **common,
            ),
            RecoveryOption(
                id="recover-gear",
                kind="recover_items",
                success_fact_ids=("passage-secured",),
                **common,
            ),
            RecoveryOption(
                id="camp-rest", kind="rest", recovery_hp=2, recovery_fp=2, ticks=2, **common
            ),
        ),
    )
    scenes = SceneRules(
        id="lantern-scenes",
        version=1,
        scenes=(
            Scene(
                id="harbor-scene",
                version=1,
                location_id="harbor",
                title="Lantern Harbor",
                exits=(SceneExit(id="customs-path", destination_id="warehouse-scene", ticks=1),),
            ),
            Scene(
                id="warehouse-scene",
                version=1,
                location_id="warehouse",
                title="Old Customs House",
                exits=(SceneExit(id="return-harbor", destination_id="harbor-scene", ticks=1),),
            ),
        ),
        discoveries=(
            Discovery(
                id="opening-notice", scene_id="harbor-scene", fact_id="opening", mode="automatic"
            ),
            Discovery(
                id="manifest-check",
                scene_id="harbor-scene",
                fact_id="manifest-found",
                mode="check",
                target_id="manifest",
            ),
            Discovery(
                id="duplicate-manifest",
                scene_id="warehouse-scene",
                fact_id="manifest-found",
                mode="automatic",
            ),
        ),
    )
    brief = GenerationBrief(
        premise="Recover the stolen relief shipment before the last ferry departs. The warden can be persuaded, outwitted, or fought. Rescue captured friends before leaving.",
        genre="Harbor mystery",
        tone="Hopeful suspense",
        duration_minutes=60,
        difficulty="standard",
    )
    graph = ScenarioGraph(
        id=identity,
        version=1,
        title="A Favor Repaid" if sequel else "The Last Lantern",
        brief=brief,
        opening_scene_id="harbor-scene",
        opening_action="Inspect the manifest, visit the customs house for another lead, or negotiate with Warden Sable. The ferry leaves at tick 12.",
        failure_consequence="The ferry departs. The relief shipment is lost for now; the campaign can continue.",
        world=world(),
        actors=(
            character("a", "Mira"),
            character("b", "Iven"),
            character("warden", "Warden Sable"),
        ),
        npc_actor_ids=("warden",),
        resources=ResourceState(
            owners=tuple(Owner(actor_id=a, capacity=100) for a in ("a", "b", "warden", "escrow")),
            items=tuple(
                Item(
                    id="blade-" + a,
                    definition_id="lantern-blade",
                    owner_id=a,
                    equipped=True,
                    ready=True,
                )
                for a in ("a", "b", "warden")
            )
            + (Item(id=identity + "-supply", definition_id="ration", owner_id="escrow"),),
        ),
        actions=base.rules,
        combat_consequences=(
            CombatConsequence(
                id="warden-yields",
                battlefield_id="guardhouse-yard",
                defeated_actor_id="warden",
                recipient_actor_ids=("a",),
                fact_ids=("passage-secured",),
            ),
        ),
        combat_attacks=base.rules.combat.attacks if base.rules.combat else (),
        scenes=scenes,
        objectives=ObjectiveRules(
            id=identity + "-objectives",
            version=1,
            deadline=1000 if sequel else 12,
            objectives=(
                Objective(
                    id="manifest",
                    title="Locate the relief manifest",
                    predicates=(Predicate(kind="known", subject_id="a", value="manifest-found"),),
                ),
                Objective(
                    id="passage",
                    title="Secure passage and recover Mira's equipment",
                    predicates=(
                        Predicate(kind="known", subject_id="a", value="passage-secured"),
                        Predicate(kind="item", subject_id="a", value="lantern-blade"),
                    ),
                ),
            ),
            rewards=(
                Reward(
                    id=identity + "-points-a",
                    actor_id="a",
                    points=4,
                    outcomes=("success", "partial-success"),
                ),
                Reward(
                    id=identity + "-points-b",
                    actor_id="b",
                    points=4,
                    outcomes=("success", "partial-success"),
                ),
                Reward(id=identity + "-supply", actor_id="a", item_id=identity + "-supply"),
            ),
        ),
        noncombat=NoncombatRules(
            id="lantern-negotiation",
            version=1,
            encounters=(
                NoncombatRule(
                    id="parley",
                    scene_id="harbor-scene",
                    category="negotiation",
                    stakes="Sable releases the relief shipment if persuaded; a failed approach costs time.",
                    required_progress=2,
                    maximum_failures=2,
                    approaches=(
                        Approach(
                            id="appeal-to-duty", check_rule_id="convince-warden", failure_progress=0
                        ),
                    ),
                    completion_fact_ids=("passage-secured",),
                ),
            ),
        ),
        party=PartyRules(id="lantern-party", version=1),
        recovery=recovery,
        npcs=NPCRules(
            id=identity + "-npcs",
            version=1,
            plans=(
                NPCPlan(
                    id=identity + "-watch",
                    actor_id="warden",
                    goal="Keep the relief shipment at the pier",
                    disposition="neutral",
                    first_due=100 if sequel else 1,
                    interval=1,
                    action_budget=12,
                    clock_limit=12,
                    actions=(NPCAction(id="watch", kind="patrol"),),
                ),
            ),
        ),
    )

    if sequel:
        # New evidence IDs prevent prior success from auto-completing the successor.
        encoded = graph.model_dump_json()
        for fact_id in (
            "manifest-found",
            "passage-secured",
            "capture-signal",
            "inside-route",
            "opening",
        ):
            encoded = encoded.replace('"' + fact_id + '"', '"' + fact_id + '-2"')
        graph = ScenarioGraph.model_validate_json(encoded).model_copy(
            update={
                "opening_action": "Repay the Ferrymen by locating the next relief manifest and securing a second passage before tick 1000.",
            }
        )
    return ScenarioGraph.model_validate_json(graph.model_dump_json())


@cache
def adventure(*, sequel: bool = False) -> ScenarioGraph:
    """Load the immutable, versioned ScenarioGraph shipped in the installed wheel."""
    name = "last-lantern-2.json" if sequel else "last-lantern-1.json"
    return ScenarioGraph.model_validate_json(
        files("wayfarer.adventures").joinpath("fixtures", name).read_text()
    )
