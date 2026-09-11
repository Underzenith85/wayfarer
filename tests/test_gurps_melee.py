"""Independent melee expectations: Lite 24-28; B374-376, B381, B556.

Test-only catalog bindings do not change profile certification or availability.
"""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_actions import campaign, world
from test_statistics import BASIC, LITE, gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.explosion_types import ExplosionSpec
from wayfarer.rules.location_types import HumanBody
from wayfarer.rules.object_types import ObjectCondition, ObjectProfile
from wayfarer.rules.recovery_types import RecoveryTask
from wayfarer.rules.skill_types import ControllingAttribute, Difficulty, SkillDefault, SkillSpec
from wayfarer.simulation.action_engine import ActionEngine
from wayfarer.simulation.actions import ActionRules, ActorSetup
from wayfarer.simulation.combat import (
    Battlefield,
    CombatRules,
    Defense,
    GridPoint,
    Placement,
    RangedSituation,
)
from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
from wayfarer.simulation.gurps_equipment import (
    LITE_EQUIPMENT,
    LITE_SOURCE,
    Damage,
    EquipmentCatalog,
    EquipmentProfile,
    MeleeMode,
    Parry,
    RangedMode,
    Shield,
)
from wayfarer.simulation.mechanics.gurps_melee import defense_value, movement
from wayfarer.simulation.resources import Item, Owner, ResourceEngine, ResourceState
from wayfarer.simulation.studio import ScenarioGraph


async def setup(
    tmp_path: Path,
    profile: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"] = "gurps-lite-4e-2004",
    *,
    trained: bool = True,
    ability_defense: bool = False,
    human: bool = False,
    free_defender_hand: bool = False,
    ranged_fixture: bool = False,
    ranged_mode: RangedMode | None = None,
    ranged_scene: tuple[RangedSituation, ...] = (),
    ready_after_attack: bool = False,
    parry: Parry | None = None,
    unarmed_fixture: bool = False,
    third_actor: bool = False,
    durability: ObjectProfile | None = None,
    object_hp: int | None = None,
    critical_breakage: Literal["ordinary", "cheap", "resistant"] | None = None,
    attacker_weight: int | None = None,
    melee_modes: tuple[MeleeMode, ...] | None = None,
    parry_quality: Literal["cheap", "good", "fine", "very-fine"] | None = None,
    physical_purchases: tuple[Purchase, ...] = (),
    darkness_penalty: int = 0,
    extra_definitions: tuple[RuleDefinition, ...] = (),
    extra_purchases: tuple[Purchase, ...] = (),
    campaign_technology_level: int | None = None,
    extra_equipment: tuple[EquipmentProfile, ...] = (),
    warhead: ExplosionSpec | None = None,
    power_cell_capacity: int | None = None,
    extra_items: tuple[Item, ...] = (),
) -> tuple[str, PlayService]:
    equipment = EquipmentCatalog(
        profile_id=profile,
        entries=(
            *LITE_EQUIPMENT.entries,
            EquipmentProfile(
                definition_id="equipment:shield",
                provenance=LITE_SOURCE,
                weight_millipounds=2000,
                price=40,
                technology_level=1,
                slot="shield",
                shield=Shield(skill_id="skill:shield", defense_bonus=1),
            ),
        ),
    )
    if melee_modes is not None:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(update={"modes": melee_modes})
                    if e.definition_id == "equipment:broadsword"
                    else e
                    for e in equipment.entries
                )
            }
        )
    if attacker_weight is not None:
        equipment = equipment.model_copy(
            update={
                "entries": equipment.entries
                + (
                    EquipmentProfile(
                        definition_id="equipment:maul",
                        provenance=LITE_SOURCE,
                        weight_millipounds=attacker_weight,
                        price=80,
                        technology_level=2,
                        slot="hand",
                        modes=(
                            MeleeMode(
                                id="swing",
                                skill_id="skill:broadsword",
                                minimum_st=10,
                                damage=Damage(basis="swing", adds=1, damage_type="cr"),
                                reach=(1,),
                                parry=Parry(),
                            ),
                        ),
                    ),
                )
            }
        )
    if ready_after_attack:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "modes": tuple(
                                m.model_copy(update={"ready_after_attack": True}) for m in e.modes
                            )
                        }
                    )
                    for e in equipment.entries
                )
            }
        )
    if parry is not None:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "modes": tuple(
                                m.model_copy(update={"parry": parry})
                                if isinstance(m, MeleeMode)
                                else m
                                for m in e.modes
                            )
                        }
                    )
                    for e in equipment.entries
                )
            }
        )
    if ranged_fixture:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "modes": e.modes
                            + (
                                RangedMode(
                                    id="throw-fixture",
                                    skill_id="skill:broadsword",
                                    minimum_st=1,
                                    damage=Damage(basis="fixed", dice=1, damage_type="cr"),
                                    accuracy=2,
                                    range_basis="yards",
                                    maximum_range=10,
                                    shots=1,
                                    reload_seconds=0,
                                    bulk=-2,
                                    thrown=True,
                                ),
                            )
                        }
                    )
                    if e.definition_id == "equipment:broadsword"
                    else e
                    for e in equipment.entries
                )
            }
        )
    if ranged_mode is not None:
        entries = tuple(
            e.model_copy(
                update={
                    "modes": e.modes + (ranged_mode,),
                    "warhead": warhead if ranged_mode.thrown else None,
                    "technology_level": ranged_mode.firearm.technology_level
                    if ranged_mode.firearm
                    else e.technology_level,
                }
            )
            if e.definition_id == "equipment:broadsword"
            else e
            for e in equipment.entries
        )
        if ranged_mode.ammunition_id:
            entries += (
                EquipmentProfile(
                    definition_id=ranged_mode.ammunition_id,
                    provenance=LITE_SOURCE,
                    weight_millipounds=10,
                    price=1,
                    technology_level=1,
                    ammunition=True,
                    warhead=warhead,
                    power_cell_capacity=power_cell_capacity,
                ),
            )
        equipment = equipment.model_copy(update={"entries": entries})
    if extra_equipment:
        equipment = equipment.model_copy(update={"entries": equipment.entries + extra_equipment})
    source = "sjg:gurps-lite-4e-2004" if profile == LITE else "sjg:basic-set-characters-4e-2004"
    if durability:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "durability": durability,
                            "parry_quality": parry_quality if e.modes else None,
                            "critical_breakage": (critical_breakage or "ordinary")
                            if e.modes
                            else None,
                        }
                    )
                    if e.modes or e.shield
                    else e
                    for e in equipment.entries
                )
            }
        )
        if durability.repair_tools_definition:
            equipment = equipment.model_copy(
                update={
                    "entries": equipment.entries
                    + (
                        EquipmentProfile(
                            definition_id=durability.repair_tools_definition,
                            provenance=LITE_SOURCE,
                            weight_millipounds=1000,
                            price=20,
                            technology_level=2,
                        ),
                    )
                }
            )
        if durability.repair_parts_definition:
            equipment = equipment.model_copy(
                update={
                    "entries": equipment.entries
                    + (
                        EquipmentProfile(
                            definition_id=durability.repair_parts_definition,
                            provenance=LITE_SOURCE,
                            weight_millipounds=10,
                            price=10,
                            technology_level=2,
                        ),
                    )
                }
            )
    if profile == BASIC:
        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "provenance": e.provenance.model_copy(
                                update={
                                    "source_id": source,
                                    "edition": "Fourth Edition, first printing (2004)",
                                }
                            )
                        }
                    )
                    for e in equipment.entries
                )
            }
        )
    skills = tuple(
        RuleDefinition(
            key,
            DefinitionKind.SKILL,
            key,
            source,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=SkillSpec(
                ControllingAttribute.DX,
                difficulty,
                "B208/B220",
                defaults=(
                    SkillDefault("attribute:dx", -5 if difficulty is Difficulty.AVERAGE else -4),
                ),
            ),
        )
        for key, difficulty in (
            ("skill:broadsword", Difficulty.AVERAGE),
            ("skill:shield", Difficulty.EASY),
            *(
                (((durability.repair_skill_id, Difficulty.AVERAGE),))
                if durability and durability.repair_skill_id
                else ()
            ),
            *(
                ((("skill:judo", Difficulty.HARD), ("skill:wrestling", Difficulty.AVERAGE)))
                if unarmed_fixture
                else ()
            ),
        )
    )
    armoury_id = (
        ranged_mode.firearm.armoury_skill_id if ranged_mode and ranged_mode.firearm else None
    )
    if armoury_id:
        skills += (
            RuleDefinition(
                armoury_id,
                DefinitionKind.SKILL,
                "Armoury (Small Arms)",
                source,
                None,
                ImplementationStatus.IMPLEMENTED,
                hooks=("character.gurps-skill", "check.target"),
                skill=SkillSpec(ControllingAttribute.IQ, Difficulty.AVERAGE, "B178/B407"),
            ),
        )
    extras = (
        skills
        + extra_definitions
        + tuple(
            RuleDefinition(
                e.definition_id,
                DefinitionKind.EQUIPMENT,
                e.definition_id,
                source,
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for e in equipment.entries
        )
    )
    from wayfarer.rules.abilities import definition
    from wayfarer.rules.traits import TraitOptions
    from wayfarer.simulation.ability_types import AbilityRules, AbilitySpec

    ability = AbilitySpec(
        definition_id="trait:shield", kind="damage-resistance", modifiers=("costs-fatigue-1",)
    )
    package = profile_package(
        profile, *extras, *((definition(ability),) if ability_defense else ())
    )
    from wayfarer.rules.physical_traits import PHYSICAL_HOOKS

    if physical_purchases:
        from wayfarer.rules.mundane_traits import candidate_package

        physical = candidate_package()
        package = replace(
            package,
            sources=package.sources + physical.sources,
            definitions=package.definitions + physical.definitions,
        )
    if critical_breakage is not None:
        from wayfarer.rules.object_types import ObjectProfile

        equipment = equipment.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(
                        update={
                            "critical_breakage": critical_breakage,
                            "durability": durability
                            or ObjectProfile(construction="unliving", hp=12, dr=4, ht=10),
                        }
                    )
                    if e.definition_id == "equipment:broadsword"
                    else e
                    for e in equipment.entries
                )
            }
        )
    catalog = RulesCatalog((package,))
    policy = CampaignPolicy(
        id="melee-test",
        version=1,
        point_budget=150,
        disadvantage_limit=100,
        attribute_ceiling=20,
        skill_ceiling=30,
        permitted_sources=frozenset(s.id for s in package.sources),
        allowed_equipment=frozenset(e.definition_id for e in equipment.entries),
        allow_supernatural=ability_defense,
        technology_level=campaign_technology_level,
    )
    rules = CampaignRules(
        edition=package.edition,
        packages=(PackagePin(package.id, package.version, package.digest),),
        policy_id=policy.id,
        policy_version=policy.version,
    )
    compiler = CharacterCompiler(
        catalog,
        rules,
        policy,
        statistics_profile=profile,
        trait_runtime_hooks=frozenset({"ability:damage-resistance", "ability:fatigue"})
        if ability_defense
        else PHYSICAL_HOOKS
        if physical_purchases
        else frozenset(),
    )
    reviewer = PowerReviewer(
        compiler, PowerPolicy(id="power", version=1, automatic_approval=True), frozenset({"gm"})
    )
    test_world = world()
    if third_actor:
        test_world = replace(
            test_world,
            entities=test_world.entities
            + (replace(next(e for e in test_world.entities if e.id == "b"), id="c"),),
        )
    actor_ids = ("a", "b", "c") if third_actor else ("a", "b")
    resources = ResourceEngine(
        test_world, catalog, rules, policy, tuple(e.inventory_spec() for e in equipment.entries)
    )
    combat = CombatRules(
        id="gurps-melee",
        version=1,
        battlefields=(
            Battlefield(
                id="dock", location_id="dock", width=4, height=4, darkness_penalty=darkness_penalty
            ),
        ),
        gurps_equipment=equipment,
    )
    engine = ActionEngine(
        reviewer,
        resources,
        ActionRules(
            id="play",
            version=1,
            maximum_wait=1800 if durability and durability.repair_skill_id else 100,
            combat=combat,
            abilities=AbilityRules(id="defense", version=1, abilities=(ability,))
            if ability_defense
            else None,
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), engine)
    initial = campaign(engine)
    purchases = (
        (
            Purchase(definition_id="skill:broadsword", amount=12),
            Purchase(definition_id="skill:shield", amount=4),
            *((Purchase(definition_id=armoury_id, amount=4),) if armoury_id else ()),
            *(
                (Purchase(definition_id=durability.repair_skill_id, amount=4),)
                if durability and durability.repair_skill_id
                else ()
            ),
            *(
                (
                    Purchase(definition_id="skill:wrestling", amount=4),
                    Purchase(definition_id="skill:judo", amount=4),
                )
                if unarmed_fixture
                else ()
            ),
        )
        if trained
        else ()
    ) + extra_purchases
    actors = tuple(
        ActorSetup(
            actor_id=a,
            body=HumanBody(anatomy="human") if human else None,
            held_item_hands=(
                (f"sword-{a}", "right-hand"),
                *(((("shield-b", "left-hand"),)) if a == "b" and not free_defender_hand else ()),
            )
            if human
            else (),
            proposal=CharacterProposal(
                draft=gurps_draft(
                    *purchases,
                    *(physical_purchases if a == "b" and not free_defender_hand else ()),
                    *(
                        (
                            Purchase(
                                definition_id=ability.definition_id,
                                amount=3,
                                trait=TraitOptions(modifiers=ability.modifiers),
                            ),
                        )
                        if ability_defense
                        else ()
                    ),
                    st_level=20 if third_actor and a == "c" else 10,
                )
            ),
        )
        for a in actor_ids
    )
    seed = ResourceState(
        owners=tuple(Owner(actor_id=a, capacity=100000) for a in actor_ids),
        items=tuple(
            Item(
                id=f"sword-{a}",
                definition_id="equipment:broadsword",
                owner_id=a,
                equipped=True,
                ready=True,
            )
            for a in actor_ids
        )
        + (
            Item(
                id="shield-b",
                definition_id="equipment:shield",
                owner_id="b",
                equipped=not free_defender_hand,
                ready=not free_defender_hand,
            ),
        ),
    )
    if attacker_weight is not None:
        seed = seed.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"definition_id": "equipment:maul"})
                    if i.id == "sword-a"
                    else i
                    for i in seed.items
                )
            }
        )
    if ranged_mode is not None and ranged_mode.ammunition_id:
        seed = seed.model_copy(
            update={
                "items": seed.items
                + (
                    Item(
                        id="ammo-a",
                        definition_id=ranged_mode.ammunition_id,
                        owner_id="a",
                        quantity=1 if power_cell_capacity else 10,
                        charges=power_cell_capacity,
                    ),
                )
            }
        )
    if extra_items:
        seed = seed.model_copy(update={"items": seed.items + extra_items})
    if durability is None and critical_breakage is not None:
        seed = seed.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"condition": ObjectCondition(hp=12)})
                    if i.definition_id == "equipment:broadsword"
                    else i
                    for i in seed.items
                )
            }
        )
    if durability:
        seed = seed.model_copy(
            update={
                "items": tuple(
                    i.model_copy(
                        update={
                            "condition": ObjectCondition(
                                hp=durability.hp if object_hp is None else object_hp
                            )
                        }
                    )
                    if not i.id.startswith("ammo-")
                    else i
                    for i in seed.items
                )
            }
        )
    if durability and durability.repair_tools_definition:
        seed = seed.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"equipped": False, "ready": False})
                    if i.id == "sword-b"
                    else i
                    for i in seed.items
                )
                + (
                    Item(
                        id="tool-b", owner_id="b", definition_id=durability.repair_tools_definition
                    ),
                )
            }
        )
    if durability and durability.repair_parts_definition:
        seed = seed.model_copy(
            update={
                "items": seed.items
                + (
                    Item(
                        id="parts-b",
                        owner_id="b",
                        definition_id=durability.repair_parts_definition,
                        quantity=30,
                    ),
                )
            }
        )
    await play.create(initial, test_world, seed, actors)
    await CombatService(play).execute(
        initial["id"],
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            ranged_situations=ranged_scene,
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
                *(
                    (Placement(actor_id="c", position=GridPoint(x=2, y=0), facing="west"),)
                    if third_actor
                    else ()
                ),
            ),
        ),
        authenticated_actor_id="gm",
    )
    return initial["id"], play


async def attack(cid: str, play: PlayService) -> None:
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )


def choice(defense: str = "none") -> ChooseDefense:
    return ChooseDefense.model_validate(
        {
            "id": "defense",
            "actor_id": "b",
            "expected_revision": 2,
            "encounter_id": "fight",
            "defense": defense,
        }
    )


async def test_trained_skill_and_deferred_restart_receipt(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await attack(cid, play)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(
        AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([4, 4, 4, 3, 3, 3, 3])
    )
    result = await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13
    assert result.injury.basic_damage == 4 and result.injury.injury == 6
    assert result.injury.hp_after == 4
    restarted.rng = RecordedDice([])
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid, choice().model_copy(update={"id": "new"}), authenticated_actor_id="b"
        )
    await CombatService(restarted).execute(
        cid,
        TakeCombatTurn(
            id="wait-b",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="b",
    )
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )


async def test_default_skill_not_dx_and_invalid_mode_no_mutation(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, trained=False)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="bad",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                target_id="b",
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before
    await attack(cid, play)
    play.rng = RecordedDice([2, 2, 3])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury is not None and result.injury.attack.effective_target == 5
    assert result.injury.injury == 0


@pytest.mark.parametrize("profile", [LITE, BASIC])
async def test_dodge_parry_block_and_repeats(
    tmp_path: Path, profile: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
) -> None:
    cid, play = await setup(tmp_path, profile)
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    expectations: tuple[tuple[Defense, int], ...] = (("dodge", 9), ("parry", 10), ("block", 10))
    for defense, expected in expectations:
        value, _ = defense_value(play.rules_context, state, defender, defense)
        assert value is not None and value.value == expected
    spent = defender.model_copy(
        update={"reaction_available": False, "parries": ("sword-b",), "block_used": True}
    )
    value, _ = defense_value(play.rules_context, state, spent, "dodge")
    assert value is not None and value.value == 9
    with pytest.raises(ValidationError):
        defense_value(play.rules_context, state, spent, "block")
    if profile == LITE:
        with pytest.raises(ValidationError):
            defense_value(play.rules_context, state, spent, "parry")
    else:
        value, _ = defense_value(play.rules_context, state, spent, "parry")
        assert value is not None and value.value == 6


async def test_heavy_weapon_requires_explicit_breakage_metadata(tmp_path: Path) -> None:
    """B376: a 3:1 parry needs quality/durability; smaller ratios need neither."""
    basic: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    cid, play = await setup(tmp_path, basic, attacker_weight=9000)
    result = await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )
    assert result.available == ("none", "dodge", "block")
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    with pytest.raises(ValidationError):
        defense_value(play.rules_context, state, defender, "parry")
    unaffected: tuple[tuple[Defense, int], ...] = (("dodge", 9), ("block", 10))
    for defense, expected in unaffected:
        value, _ = defense_value(play.rules_context, state, defender, defense)
        assert value is not None and value.value == expected
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    assert await play.store.read(cid) == before

    cid, play = await setup(tmp_path / "under", basic, attacker_weight=8999)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    value, item = defense_value(play.rules_context, state, defender, "parry")
    assert value is not None and value.value == 10 and item == "sword-b"

    cid, play = await setup(tmp_path / "lite", attacker_weight=9000)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    value, _ = defense_value(play.rules_context, state, defender, "parry")
    assert value is not None and value.value == 10


async def test_unbalanced_and_fencing_parry_columns(tmp_path: Path) -> None:
    """B271 parry-column footnotes: unbalanced blocks a same-turn parry, fencing halves repeats."""
    basic: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    cid, play = await setup(tmp_path, basic, parry=Parry(fencing=True))
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    repeated = defender.model_copy(update={"parries": ("sword-b",)})
    value, _ = defense_value(play.rules_context, state, repeated, "parry")
    assert value is not None and value.value == 8  # 10 - 2, not the ordinary -4.

    cid, play = await setup(tmp_path / "unbalanced", basic, parry=Parry(unbalanced=True))
    state = play._load(await play.store.read(cid))
    defender = state.encounters[0].participants[1]
    value, _ = defense_value(play.rules_context, state, defender, "parry")
    assert value is not None and value.value == 10
    attacked = defender.model_copy(
        update={"last_maneuver": "attack", "last_attack_item_id": "sword-b"}
    )
    with pytest.raises(ValidationError):
        defense_value(play.rules_context, state, attacked, "parry")


async def test_lite_critical_bypasses_defense_and_uses_maximum(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await attack(cid, play)
    play.rng = RecordedDice([1, 1, 1, 3, 3, 3])
    result = await CombatService(play).execute(cid, choice("dodge"), authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.defense is None and not result.injury.damage_dice
    assert result.injury.basic_damage == 7 and result.injury.hp_after == 0


async def test_basic_critical_miss_is_persisted_blocker_not_reroll(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, 2, 2, 1])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    assert result.injury is not None and result.injury.critical_table == (2, 2, 1)
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].blocked_reason == "basic-critical-miss:5"
    assert not result.available
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == result


@pytest.mark.parametrize(
    "table,expected",
    [((3, 3, 3), "drop"), ((2, 3, 3), "unready"), ((2, 2, 3), "balance"), ((5, 5, 6), "prone")],
)
async def test_basic_ordinary_critical_misses_execute(
    tmp_path: Path, table: tuple[int, int, int], expected: str
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, *table])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert result.injury is not None and result.injury.adjudication_required is None
    a = state.encounters[0].participants[0]
    item = next(i for i in state.resources.items if i.id == "sword-a")
    if expected in ("drop", "unready"):
        assert not item.ready and item.equipped is (expected == "unready")
    elif expected == "balance":
        assert a.defense_penalty == -2
    else:
        assert a.posture == "prone"


async def test_unauthorized_defense_and_forged_damage_never_roll(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await attack(cid, play)
    play.rng = RecordedDice([])
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, choice(), authenticated_actor_id="a")
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid, {**choice().model_dump(), "basic_damage": 999}, authenticated_actor_id="b"
        )
    assert before == await play.store.read(cid)


async def test_maximum_length_command_id_and_retry(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    attack_command = TakeCombatTurn(
        id="a" * 200,
        actor_id="a",
        expected_revision=1,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    await CombatService(play).execute(cid, attack_command, authenticated_actor_id="a")
    play.rng = RecordedDice([2, 2, 3, 1])
    command = choice().model_copy(update={"id": "d" * 200})
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result


async def test_injury_stun_recovers_on_forced_turn_without_repeating_damage(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await attack(cid, play)
    play.rng = RecordedDice([4, 4, 4, 3, 4, 4, 4])
    await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    before = play._load(await play.store.read(cid))
    hp = next(p for p in before.resources.pools if p.id == "hp:b")
    assert hp.injury is not None and hp.injury.stunned and hp.injury.shock == 4
    play.rng = RecordedDice([3, 3, 3])
    command = TakeCombatTurn(
        id="recover", actor_id="b", expected_revision=3, encounter_id="fight", maneuver="do_nothing"
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    after = play._load(await play.store.read(cid))
    healed = next(p for p in after.resources.pools if p.id == "hp:b")
    assert healed.current == hp.current == 4
    assert healed.injury is not None and not healed.injury.stunned and healed.injury.shock == 0


async def test_failed_consciousness_turn_is_committed_and_ends_encounter(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([1, 1, 1, 1, 1, 1, 3, 3, 3, 3])
    await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    play.rng = RecordedDice([4, 4, 4])
    command = TakeCombatTurn(
        id="collapse",
        actor_id="b",
        expected_revision=3,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-b",
        mode_id="swing",
        target_id="a",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].status == "completed"
    assert not result.available
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result


async def spend_fp(
    play: PlayService, cid: str, actor_id: str, amount: int, *, care_target: str | None = None
) -> None:
    snapshot = await play.store.read(cid)

    def commit(campaign: Campaign) -> Event:
        state = play._load(campaign)
        resources, _ = apply_fatigue(
            state.resources,
            FatigueCost(
                id="fixture-fp", actor_id=actor_id, expected_revision=state.revision, amount=amount
            ),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
        if care_target is not None:
            resources = resources.model_copy(
                update={
                    "recovery_tasks": (
                        RecoveryTask(
                            id="due-care",
                            actor_id=care_target,
                            target_id=care_target,
                            profile_id="gurps-lite-4e-2004",
                            kind="bandage",
                            start=0,
                            due=1,
                        ),
                    ),
                    "game_time": 1,
                }
            )
        updated = state.model_copy(update={"revision": resources.revision, "resources": resources})
        play.engine.validate(updated)
        campaign["revision"], campaign["play_json"] = updated.revision, updated.model_dump_json()
        return Event(input="fixture-fp", action="resource", outcome="exertion", roll=None)

    await play.store.commit_turn(cid, "fixture-fp", snapshot["revision"], "fixture-fp", commit)


async def test_low_fp_reduces_dodge_and_move_from_authoritative_pools(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await spend_fp(play, cid, "b", 7)
    state = play._load(await play.store.read(cid))
    value, _ = defense_value(
        play.rules_context, state, state.encounters[0].participants[1], "dodge"
    )
    assert value is not None and value.value == 5
    assert movement(play.rules_context, state, "b") == 3


async def test_target_due_care_blocks_attack_before_any_dice_or_state_change(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    await spend_fp(play, cid, "a", 0, care_target="b")
    play.rng = RecordedDice([])
    before = await play.store.read(cid)
    command = TakeCombatTurn(
        id="attack-due-target",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    with pytest.raises(ConflictError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert await play.store.read(cid) == before


async def test_failed_fatigue_check_commits_collapse_without_attack_or_retry_roll(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    await spend_fp(play, cid, "a", 10)
    play.rng = RecordedDice([4, 4, 4])
    command = TakeCombatTurn(
        id="exhausted-attack",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].status == "completed" and not state.encounters[0].wounds
    fp = next(p for p in state.resources.pools if p.id == "fp:a")
    assert fp.fatigue is not None and fp.fatigue.collapsed
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result


async def test_low_fp_st_penalty_does_not_reduce_damage(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await spend_fp(play, cid, "a", 7)
    command = TakeCombatTurn(
        id="tired-attack",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    play.rng = RecordedDice([2, 2, 3, 3, 3, 3, 3])
    result = await CombatService(play).execute(
        cid, choice().model_copy(update={"expected_revision": 3}), authenticated_actor_id="b"
    )
    assert result.injury is not None
    assert result.injury.attack.effective_target == 8
    assert result.injury.basic_damage == 4 and result.injury.injury == 6


async def test_authored_combat_catalog_survives_roundtrip_without_changing_prototype(
    tmp_path: Path,
) -> None:
    from wayfarer.adventures.lantern import adventure

    _, play = await setup(tmp_path)
    rules = play.engine.rules.combat
    assert rules is not None and rules.gurps_equipment is not None
    original = adventure()
    assert "combat_equipment" not in original.model_dump()
    graph = original.model_copy(
        update={
            "actions": original.actions.model_copy(update={"combat": rules}),
            "combat_equipment": rules.gurps_equipment,
            "combat_attacks": (),
            "combat_protection": (),
            "combat_consequences": (),
        }
    )
    restored = ScenarioGraph.model_validate_json(graph.model_dump_json())
    rebound = restored.runtime_rules().combat
    assert rebound is not None and rebound.gurps_equipment == rules.gurps_equipment


@pytest.mark.parametrize(
    "table,die,basic,injury",
    [
        ((1, 1, 1), 3, 12, 18),
        ((1, 2, 2), 3, 8, 12),
        ((2, 2, 2), 0, 7, 10),
        ((3, 3, 3), 3, 4, 6),
        ((4, 4, 4), 3, 4, 6),
        ((5, 5, 6), 3, 8, 12),
    ],
)
async def test_basic_critical_hit_golden_damage(
    tmp_path: Path, table: tuple[int, int, int], die: int, basic: int, injury: int
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([1, 1, 1, *table, *([die] if die else []), 3, 3, 3])
    result = await CombatService(play).execute(cid, choice("block"), authenticated_actor_id="b")
    assert result.injury is not None and result.injury.defense is None
    assert (result.injury.basic_damage, result.injury.injury, result.injury.hp_after) == (
        basic,
        injury,
        10 - injury,
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.pools[2].current == 10 - injury
    if sum(table) == 12:
        assert not any(i.ready for i in state.resources.items if i.owner_id == "b")
