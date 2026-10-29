"""Roadside Watch: one authored, source-pinned human campaign.

The initial facts, equipment and awareness are trusted GM provisions. Checks,
combat wounds, treatment and endings must arise through native commands. This
module does not declare full Basic Set coverage or apply surprise from Stealth.
"""

from functools import cache
from importlib.resources import files

from wayfarer.adventures.roadwatch_rules import (
    CATALOG,
    EQUIPMENT,
    EQUIPMENT_SPECS,
    NPC_ACTOR_ID,
    PACKAGE,
    PC_ACTOR_IDS,
    POLICY,
    PROFILE_ID,
    RULES,
    SOURCE_BASELINE,
    proposal,
    reviewer,
)
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, CheckRule
from wayfarer.engine.simulation.campaign.objectives import Objective, ObjectiveRules, Predicate
from wayfarer.engine.simulation.campaign.scenes import Discovery, Scene, SceneExit, SceneRules
from wayfarer.engine.simulation.campaign.studio import (
    ApproachSupport,
    GenerationBrief,
    ScenarioGraph,
)
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState
from wayfarer.engine.simulation.social.noncombat import Approach, NoncombatRule, NoncombatRules
from wayfarer.engine.world import Connection, Entity, EntityKind, Fact, World

SCENARIO_ID = "roadside-watch-1"
BATTLEFIELD_ID = "roadwatch-clearing"
CARE_LOCATION_ID = "shelter"
BANDAGES_ITEM_ID = "bandages-post"
STERILITY_FACT_ID = "bandages-post-sterile"
PUBLIC_ACTOR_FACT_IDS = tuple(
    f"roadwatch-identity-{actor}" for actor in (*PC_ACTOR_IDS, NPC_ACTOR_ID)
)


def world() -> World:
    """Authoring-public setting; no confidential NPC fact is in this template."""
    return World(
        entities=(
            Entity("post", EntityKind.LOCATION, "Roadwatch Post"),
            Entity("road", EntityKind.LOCATION, "The Road Clearing"),
            Entity("shelter", EntityKind.LOCATION, "Wayside Shelter"),
            Entity("a", EntityKind.ACTOR, "Mira", location_id="post"),
            Entity("b", EntityKind.ACTOR, "Iven", location_id="post"),
            Entity(NPC_ACTOR_ID, EntityKind.ACTOR, "Rook the Brigand", location_id="road"),
            Entity("road-view", EntityKind.OBJECT, "The roadside lookout", location_id="post"),
            Entity("screened-path", EntityKind.OBJECT, "The screened footpath", location_id="post"),
            Entity(
                "aid-station",
                EntityKind.OBJECT,
                "A warm, quiet treatment space",
                location_id="shelter",
            ),
            Entity(BANDAGES_ITEM_ID, EntityKind.OBJECT, "Sterile bandages", owner_id="b"),
        ),
        connections=(
            Connection("post", "road", "a ten-yard footpath"),
            Connection("road", "post", "the return footpath"),
            Connection("road", "shelter", "a ten-yard side path"),
            Connection("shelter", "road", "the side path back to the clearing"),
        ),
        # Public identity is distinct from current visibility. Each participant
        # knows who the other openly announced participants are; location and
        # encounter geometry still decide whether a present target can be seen.
        facts=tuple(
            Fact(f"roadwatch-identity-{actor_id}", actor_id, "identity", description)
            for actor_id, description in (
                ("a", "Mira is a publicly identified roadwatch volunteer."),
                ("b", "Iven is a publicly identified roadwatch volunteer."),
                (NPC_ACTOR_ID, "Rook is the openly identified brigand in this survey."),
            )
        )
        + (
            Fact(
                "opening",
                "post",
                "briefing",
                "Survey the road and return to the shelter. Rook is openly waiting in the clearing; neither side is surprised.",
            ),
            Fact(
                "road-warning",
                "road",
                "observation",
                "From the lookout you observe Rook's broadsword and lack of armor. No concealed detail or private motive is revealed.",
            ),
            Fact(
                "quiet-approach",
                "screened-path",
                "crossing",
                "You crossed the screened path quietly at a slow pace. This grants no concealment, surprise or combat modifier.",
            ),
            Fact(
                "noisy-approach",
                "screened-path",
                "crossing",
                "Your footfalls were audible on the screened path. You may continue the survey; no surprise or combat modifier follows.",
            ),
            Fact(
                STERILITY_FACT_ID,
                BANDAGES_ITEM_ID,
                "condition",
                "clean sterile bandages",
            ),
            Fact(
                "aid-station-ready",
                "aid-station",
                "care",
                "A warm, comfortable, quiet place for ordinary human wound care, with no superior equipment bonus.",
            ),
        ),
        knowledge=tuple(
            (actor_id, fact_id)
            for actor_id in PC_ACTOR_IDS
            for fact_id in ("opening", STERILITY_FACT_ID)
        )
        + tuple(
            (actor_id, fact_id)
            for actor_id in (*PC_ACTOR_IDS, NPC_ACTOR_ID)
            for fact_id in PUBLIC_ACTOR_FACT_IDS
        ),
    )


def _actions() -> ActionRules:
    return ActionRules(
        id="roadwatch-actions",
        version=1,
        maximum_wait=3600,
        checks=(
            CheckRule(
                id="watch-road",
                action="inspect",
                target_id="road-view",
                definition_id="skill:observation",
                package_id=PACKAGE.id,
                package_version=PACKAGE.version,
                duration=10,
                reveal_fact_ids=("road-warning",),
            ),
            CheckRule(
                id="cross-quietly",
                action="inspect",
                target_id="screened-path",
                definition_id="skill:stealth",
                package_id=PACKAGE.id,
                package_version=PACKAGE.version,
                duration=10,
            ),
        ),
        combat=CombatRules(
            id="roadwatch-combat",
            version=1,
            max_combatants=3,
            battlefields=(
                HexBattlefield(
                    id=BATTLEFIELD_ID,
                    location_id="road",
                    coordinate_system="hex-axial-v1",
                    profile_id=PROFILE_ID,
                    baseline_id=SOURCE_BASELINE,
                    cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(8) for r in range(6)),
                ),
            ),
            gurps_equipment=EQUIPMENT,
        ),
    )


def engine() -> ActionEngine:
    """Compose the existing authoritative engine with exact limited source pins."""
    return ActionEngine(
        reviewer(),
        ResourceEngine(world(), CATALOG, RULES, POLICY, EQUIPMENT_SPECS),
        _actions(),
    )


def _actor(actor_id: str) -> ActorSetup:
    return ActorSetup(
        actor_id=actor_id,
        proposal=proposal(actor_id),
        body=HumanBody(anatomy="human"),
        held_item_hands=((f"sword-{actor_id}", "right-hand"),),
        # Knowing the confrontation is coming prevents an unverified surprise
        # shortcut. Observation and the earlier quiet-path trial add no bonus.
        aware_of=(
            "a",
            "b",
            NPC_ACTOR_ID,
            "post",
            "road",
            "shelter",
            "road-view",
            "screened-path",
            "aid-station",
            BANDAGES_ITEM_ID,
        ),
    )


def _item(
    item_id: str,
    definition_id: str,
    owner_id: str,
    *,
    equipped: bool = False,
    ready: bool = False,
) -> Item:
    spec = next(value for value in EQUIPMENT_SPECS if value.definition_id == definition_id)
    return Item(
        id=item_id,
        definition_id=definition_id,
        owner_id=owner_id,
        equipped=equipped,
        ready=ready,
        condition=ObjectCondition(hp=spec.durability.hp) if spec.durability is not None else None,
    )


def build_adventure() -> ScenarioGraph:
    """Build the trusted initial graph; never seed injuries or played outcomes."""
    graph = ScenarioGraph(
        id=SCENARIO_ID,
        version=1,
        title="Roadside Watch",
        brief=GenerationBrief(
            premise="Mira and Iven survey a road clearing, face an openly waiting brigand, and return to the wayside shelter before the next day.",
            genre="Low-tech road adventure",
            tone="Grounded",
            duration_minutes=45,
            difficulty="gentle",
            restrictions=(
                "One player controls both human PCs; a separate GM controls Rook.",
                "Ordinary melee only; no magic, shields, ranged weapons or surprise bonuses.",
                "Starting equipment is GM-issued; trade, wealth and advancement are outside this episode.",
                "Clinical treatment uses actual ordinary sterile bandages at the shelter.",
            ),
        ),
        opening_scene_id="post-scene",
        opening_action="Inspect the roadside lookout, optionally try the screened path, then travel to the clearing. The brigand is aware of you. Return to the shelter when the encounter is lawfully resolved.",
        failure_consequence="You may withdraw or abandon the survey. A failed optional check does not lock the shelter route or invent a successful report.",
        world=world(),
        actors=tuple(_actor(actor_id) for actor_id in (*PC_ACTOR_IDS, NPC_ACTOR_ID)),
        npc_actor_ids=(NPC_ACTOR_ID,),
        resources=ResourceState(
            owners=tuple(
                Owner(actor_id=actor_id, capacity=100_000)
                for actor_id in (*PC_ACTOR_IDS, NPC_ACTOR_ID)
            ),
            items=tuple(
                _item(
                    f"sword-{actor_id}",
                    "equipment:broadsword",
                    actor_id,
                    equipped=True,
                    ready=True,
                )
                for actor_id in (*PC_ACTOR_IDS, NPC_ACTOR_ID)
            )
            + tuple(
                _item(
                    f"armor-{actor_id}",
                    "equipment:leather-armor",
                    actor_id,
                    equipped=True,
                )
                for actor_id in PC_ACTOR_IDS
            )
            + (_item(BANDAGES_ITEM_ID, "equipment:bandages", "b"),),
        ),
        actions=_actions(),
        combat_equipment=EQUIPMENT,
        scenes=SceneRules(
            id="roadwatch-scenes",
            version=2,
            interrupt_recovery_on_travel=True,
            scenes=(
                Scene(
                    id="post-scene",
                    version=1,
                    location_id="post",
                    title="Roadwatch Post",
                    exits=(SceneExit(id="post-to-road", destination_id="road-scene", ticks=10),),
                ),
                Scene(
                    id="road-scene",
                    version=1,
                    location_id="road",
                    title="The Road Clearing",
                    exits=(
                        SceneExit(id="road-to-post", destination_id="post-scene", ticks=10),
                        SceneExit(id="road-to-shelter", destination_id="shelter-scene", ticks=10),
                    ),
                ),
                Scene(
                    id="shelter-scene",
                    version=1,
                    location_id="shelter",
                    title="Wayside Shelter",
                    exits=(SceneExit(id="shelter-to-road", destination_id="road-scene", ticks=10),),
                ),
            ),
            discoveries=(
                Discovery(
                    id="shelter-care-space",
                    scene_id="shelter-scene",
                    fact_id="aid-station-ready",
                    mode="automatic",
                ),
            ),
        ),
        objectives=ObjectiveRules(
            id="roadwatch-objectives",
            version=1,
            deadline=86400,
            objectives=tuple(
                Objective(
                    id=f"return-{actor_id}",
                    title=f"Return {name} to the shelter",
                    predicates=(Predicate(kind="location", subject_id=actor_id, value="shelter"),),
                )
                for actor_id, name in (("a", "Mira"), ("b", "Iven"))
            )
            + (
                Objective(
                    id="report-observation",
                    title="Iven records the road observation",
                    required=False,
                    predicates=(Predicate(kind="known", subject_id="b", value="road-warning"),),
                ),
                Objective(
                    id="report-quiet-path",
                    title="Iven records a quiet crossing",
                    required=False,
                    predicates=(Predicate(kind="known", subject_id="b", value="quiet-approach"),),
                ),
            ),
        ),
        noncombat=NoncombatRules(
            id="roadwatch-quiet-path",
            version=1,
            encounters=(
                NoncombatRule(
                    id="quiet-path",
                    scene_id="post-scene",
                    category="infiltration",
                    stakes="Cross the screened path slowly and quietly. Nobody here is an alerted sentinel; this optional trial grants no concealment, surprise or combat bonus.",
                    required_progress=1,
                    maximum_failures=1,
                    approaches=(
                        Approach(
                            id="move-quietly",
                            check_rule_id="cross-quietly",
                            success_progress=1,
                            failure_progress=0,
                            success_fact_ids=("quiet-approach",),
                            failure_fact_ids=("noisy-approach",),
                        ),
                    ),
                ),
            ),
        ),
        approaches=(
            ApproachSupport(
                id="quiet-path-b",
                scene_id="post-scene",
                check_rule_id="cross-quietly",
                actor_id="b",
            ),
        ),
    )
    # Match the raw setup contract. Top-level combat_equipment is authoritative;
    # legacy ActionRules serializers deliberately omit its nested copy.
    return ScenarioGraph.model_validate_json(graph.model_dump_json())


@cache
def adventure() -> ScenarioGraph:
    """Load the exact installed raw graph, including explicit anatomy and grips."""
    return ScenarioGraph.model_validate_json(
        files("wayfarer.adventures").joinpath("fixtures", "roadwatch.json").read_text()
    )
