"""B58 materials, failed devices, actual backfire and private deterministic receipts."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gizmos import session
from test_mundane_traits import combined_package, runtime_compiler
from test_resources import engine
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Pool, ResourceState
from wayfarer.engine.simulation.traits.gadgeteer_gizmos import (
    GadgeteerGizmoApproval,
    GizmoMaterial,
    craft_gizmo,
    gm_gizmo_rolls,
)
from wayfarer.engine.simulation.traits.gizmos import RevealGizmo
from wayfarer.errors import ConflictError, ValidationError


def gizmo_package() -> RulesPackage:
    package = combined_package()
    skill = RuleDefinition(
        "skill:carpentry",
        DefinitionKind.SKILL,
        "Carpentry",
        package.sources[0].id,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.IQ, Difficulty.EASY, "B183"),
    )
    equipment = tuple(
        RuleDefinition(
            identity,
            DefinitionKind.EQUIPMENT,
            identity,
            package.sources[0].id,
            0,
            ImplementationStatus.IMPLEMENTED,
        )
        for identity in ("arrow", "bag", "sword")
    )
    return replace(package, definitions=package.definitions + (skill,) + equipment)


def gizmo_compiler() -> CharacterCompiler:
    base = runtime_compiler()
    package = gizmo_package()
    rules = replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),))
    return CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        replace(base.policy, allowed_equipment=frozenset({"arrow", "bag", "sword"})),
        statistics_profile="gurps-basic-set-4e-2004",
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )


def approved() -> tuple[ValidatedBuild, dict[str, RuleDefinition]]:
    compiler = gizmo_compiler()
    result = compiler.compile(
        gurps_draft(
            Purchase(definition_id="trait:advantage:gizmos"),
            Purchase(definition_id="trait:advantage:gadgeteer"),
            Purchase(definition_id="skill:carpentry", amount=1),
        )
    )
    assert result.build is not None, result.diagnostics
    return result.build, dict(compiler.definitions)


def context() -> tuple[ResourceEngine, ResourceState, GadgeteerGizmoApproval]:
    resources = session().model_copy(
        update={
            "items": (Item(id="raw", definition_id="arrow", owner_id="a", quantity=3),),
            "pools": (
                Pool(
                    id="hp:a",
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human"),
                ),
            ),
        }
    )
    resource_engine = engine()
    resource_engine.specs["bag"] = resource_engine.specs["bag"].model_copy(
        update={
            "durability": ObjectProfile(construction="unliving", hp=5, dr=0, ht=10),
        }
    )
    approval = GadgeteerGizmoApproval(
        id="craft",
        actor_id="a",
        session_id="one",
        item=Item(id="device", definition_id="bag", owner_id="a", condition=ObjectCondition(hp=5)),
        small_invention=True,
        own_invention=True,
        build_on_spot=True,
        materials=(GizmoMaterial(item_id="raw", quantity=2),),
        required_skill_ids=("skill:carpentry",),
        relevant_skill_id="skill:carpentry",
        backfire_damage=2,
    )
    return resource_engine, resources, approval


@pytest.mark.parametrize(
    "dice,disabled,hp", [((1, 2, 3), False, 10), ((4, 4, 4), True, 10), ((6, 6, 6), True, 8)]
)
def test_secret_build_roll_consumes_materials_and_use_with_actual_device(
    dice: tuple[int, ...], disabled: bool, hp: int
) -> None:
    resource_engine, before, approval = context()
    build, definitions = approved()
    command = RevealGizmo(
        id="make",
        actor_id="a",
        expected_revision=before.revision,
        session_id="one",
        eligibility_id="craft",
    )
    after, outcome = craft_gizmo(
        resource_engine,
        before,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(dice),
        authorized_actor_id="a",
        system=True,
    )
    assert after.items[0].id == "raw" and after.items[0].quantity == 1
    device = after.items[1]
    assert device.id == "device" and device.owner_id == "a"
    assert bool(device.condition and device.condition.disabled) is disabled
    assert after.pools[0].current == hp and outcome.uses_remaining == 0
    assert "check" not in outcome.model_dump()
    with pytest.raises(ValidationError, match="GM visibility"):
        gm_gizmo_rolls(after)
    rolls = gm_gizmo_rolls(after, gm_authorized=True)
    assert rolls[0].check is not None and rolls[0].check.dice == dice
    assert rolls[0].check.effective_target == 8
    assert rolls[0].hp_lost == 10 - hp
    restored = ResourceState.model_validate_json(after.model_dump_json())
    assert craft_gizmo(
        resource_engine,
        restored,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    ) == (restored, outcome)
    with pytest.raises(ValidationError, match="authority"):
        craft_gizmo(
            resource_engine,
            restored,
            command,
            build,
            definitions,
            approval,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=False,
        )
    assert before.items[0].quantity == 3 and before.pools[0].current == 10


def test_material_and_learned_skill_preflight_and_session_exhaustion() -> None:
    resource_engine, resources, approval = context()
    build, definitions = approved()
    command = RevealGizmo(
        id="make",
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    for invalid in (
        approval.model_copy(update={"materials": (GizmoMaterial(item_id="raw", quantity=4),)}),
        approval.model_copy(update={"required_skill_ids": ("skill:engineering",)}),
        approval.model_copy(update={"small_invention": False}),
    ):
        with pytest.raises(ValidationError):
            craft_gizmo(
                resource_engine,
                resources,
                command,
                build,
                definitions,
                invalid,
                rng=RecordedDice(()),
                authorized_actor_id="a",
                system=True,
            )
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ValidationError, match="available use"):
        craft_gizmo(
            resource_engine,
            after,
            command.model_copy(update={"id": "second", "expected_revision": after.revision}),
            build,
            definitions,
            approval,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ConflictError, match="already used"):
        craft_gizmo(
            resource_engine,
            after,
            command.model_copy(update={"eligibility_id": "changed"}),
            build,
            definitions,
            approval,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
        )


def test_owned_invention_preserves_identity_condition_without_a_crafting_roll() -> None:
    resource_engine, resources, approval = context()
    build, definitions = approved()
    item = approval.item.model_copy(update={"condition": ObjectCondition(hp=3)})
    approval = approval.model_copy(
        update={
            "build_on_spot": False,
            "item": item,
            "materials": (),
            "required_skill_ids": (),
            "relevant_skill_id": None,
        }
    )
    command = RevealGizmo(
        id="reveal",
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    after, outcome = craft_gizmo(
        resource_engine,
        resources,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    )
    assert after.items[0].quantity == 3 and after.items[1] == item
    assert (
        outcome.uses_remaining == 0 and gm_gizmo_rolls(after, gm_authorized=True)[0].check is None
    )


def test_long_critical_command_has_bounded_events_and_exact_replay() -> None:
    resource_engine, resources, approval = context()
    build, definitions = approved()
    command = RevealGizmo(
        id="x" * 200,
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    after, outcome = craft_gizmo(
        resource_engine,
        resources,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice((6, 6, 6)),
        authorized_actor_id="a",
        system=True,
    )
    assert after.pools[0].current == 8
    assert all(len(event.id) <= 200 for event in after.events)
    assert craft_gizmo(
        resource_engine,
        after,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    ) == (after, outcome)


def test_actual_recipe_frees_capacity_before_creating_device_and_uses_worse_modifier() -> None:
    resource_engine, resources, approval = context()
    resources = resources.model_copy(
        update={
            "owners": tuple(owner.model_copy(update={"capacity": 3}) for owner in resources.owners)
        }
    )
    build, definitions = approved()
    command = RevealGizmo(
        id="capacity",
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        command,
        build,
        definitions,
        approval.model_copy(update={"skill_modifier": -4}),
        rng=RecordedDice((1, 2, 3)),
        authorized_actor_id="a",
        system=True,
    )
    assert resource_engine.carried_weight(after, "a") == 3
    assert after.revision == resources.revision + 1
    roll = gm_gizmo_rolls(after, gm_authorized=True)[0]
    assert roll.check is not None and roll.check.effective_target == 6


@pytest.mark.parametrize(
    "unavailable", ["foreign", "world-ground", "reserved", "repair", "missing"]
)
def test_actual_material_custody_and_reservations_reject_before_entropy(unavailable: str) -> None:
    from test_actions import world

    from wayfarer.engine.simulation.equipment.repairs import RepairTask, record
    from wayfarer.engine.simulation.resources import AmmunitionLoad

    resource_engine, resources, approval = context()
    resource_engine = resource_engine.for_world(world())
    material = resources.items[0]
    if unavailable == "foreign":
        resources = resources.model_copy(
            update={"items": (material.model_copy(update={"owner_id": "b"}),)}
        )
    elif unavailable == "world-ground":
        resources = resources.model_copy(
            update={"items": (material.model_copy(update={"world_ground_location_id": "dock"}),)}
        )
    elif unavailable == "reserved":
        resources = resources.model_copy(
            update={
                "items": resources.items
                + (Item(id="weapon", definition_id="sword", owner_id="a"),),
                "ammunition_loads": (
                    AmmunitionLoad(
                        weapon_id="weapon", mode_id="shot", ammunition_item_id="raw", rounds=2
                    ),
                ),
            }
        )
    elif unavailable == "repair":
        resources = record(
            resources,
            RepairTask(
                id="repair",
                actor_id="a",
                item_id="broken",
                tool_id="raw",
                start=0,
                due=10,
                skill=10,
                condition=ObjectCondition(hp=1),
            ),
            "repair-start",
        )
    else:
        resources = resources.model_copy(update={"items": ()})
    build, definitions = approved()
    command = RevealGizmo(
        id="unavailable",
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    before = resources.model_dump_json()
    with pytest.raises((ValidationError, ConflictError)):
        craft_gizmo(
            resource_engine,
            resources,
            command,
            build,
            definitions,
            approval,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
        )
    assert resources.model_dump_json() == before


def test_attribute_purchase_is_not_a_learned_crafting_skill() -> None:
    resource_engine, resources, approval = context()
    build, definitions = approved()
    approval = approval.model_copy(
        update={"required_skill_ids": ("attribute:iq",), "relevant_skill_id": "attribute:iq"}
    )
    with pytest.raises(ValidationError, match="learned"):
        craft_gizmo(
            resource_engine,
            resources,
            RevealGizmo(
                id="wrong-skill",
                actor_id="a",
                expected_revision=resources.revision,
                session_id="one",
                eligibility_id="craft",
            ),
            build,
            definitions,
            approval,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
        )


def test_failed_invention_cannot_function_as_equipment_or_container() -> None:
    from test_actions import world

    from wayfarer.engine.rules.types.electronics import CommunicatorSpec, ElectronicsSuite
    from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
    from wayfarer.engine.simulation.equipment.electronics import (
        Communicate,
        ElectronicsContext,
        apply_electronics,
    )
    from wayfarer.engine.simulation.equipment.general import (
        UseGeneralEquipment,
        apply_general_equipment,
    )
    from wayfarer.engine.simulation.resources import Transfer

    resource_engine, resources, approval = context()
    resource_engine.specs["bag"] = resource_engine.specs["bag"].model_copy(
        update={
            "general": (GeneralEquipmentFeature(kind="communication"),),
            "electronics": ElectronicsSuite(communicator=CommunicatorSpec(media=("voice",))),
        }
    )
    build, definitions = approved()
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="failed",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ValidationError, match="Disabled"):
        apply_general_equipment(
            resource_engine,
            after,
            UseGeneralEquipment(
                id="use", actor_id="a", expected_revision=after.revision, item_id="device"
            ),
            actor_technology_level=8,
        )
    with pytest.raises(ValidationError, match="Disabled"):
        apply_electronics(
            resource_engine,
            after,
            world(),
            Communicate(
                id="call",
                actor_id="a",
                expected_revision=after.revision,
                item_id="device",
                link_id="link",
                fact_ids=(),
                medium="voice",
            ),
            ElectronicsContext(skills=()),
            system=True,
            rng=RecordedDice(()),
        )
    with pytest.raises(ValidationError, match="Disabled"):
        resource_engine.apply(
            after,
            Transfer(
                id="store",
                actor_id="a",
                expected_revision=after.revision,
                item_id="raw",
                quantity=1,
                owner_id="a",
                container_id="device",
            ),
        )
    assert after.items[-1].condition is not None and after.items[-1].condition.disabled


def test_invention_custody_and_session_reset_preserve_identity_and_failure_use() -> None:
    from wayfarer.engine.simulation.resources import Transfer
    from wayfarer.engine.simulation.traits.gizmos import BeginGizmoSession, begin_session

    resource_engine, resources, approval = context()
    build, definitions = approved()
    reveal = approval.model_copy(
        update={
            "build_on_spot": False,
            "item": approval.item.model_copy(update={"condition": ObjectCondition(hp=3)}),
        }
    )
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="existing",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        reveal,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    )
    transferred = resource_engine.apply(
        after,
        Transfer(
            id="give",
            actor_id="a",
            expected_revision=after.revision,
            item_id="device",
            quantity=1,
            owner_id="b",
        ),
    )
    device = next(item for item in transferred.items if item.id == "device")
    assert device.owner_id == "b" and device.condition == ObjectCondition(hp=3)
    reset, _ = begin_session(
        transferred,
        BeginGizmoSession(
            id="next", actor_id="gm", expected_revision=transferred.revision, session_id="two"
        ),
        authorized_actor_id="gm",
        system=True,
    )
    assert next(item for item in reset.items if item.id == "device") == device
    next_approval = approval.model_copy(
        update={
            "id": "next-craft",
            "session_id": "two",
            "item": approval.item.model_copy(update={"id": "device-two"}),
        }
    )
    after, result = craft_gizmo(
        resource_engine,
        reset,
        RevealGizmo(
            id="new-device",
            actor_id="a",
            expected_revision=reset.revision,
            session_id="two",
            eligibility_id="next-craft",
        ),
        build,
        definitions,
        next_approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    assert result.uses_remaining == 0
    assert len(gm_gizmo_rolls(after, gm_authorized=True)) == 2


def test_failed_gizmo_cannot_supply_working_fuel() -> None:
    from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
    from wayfarer.engine.simulation.equipment.general import (
        UseGeneralEquipment,
        apply_general_equipment,
    )

    resource_engine, resources, approval = context()
    resource_engine.specs["sword"] = resource_engine.specs["sword"].model_copy(
        update={
            "general": (
                GeneralEquipmentFeature(
                    kind="light", consumable_definition_id="bag", consumable_units=1
                ),
            ),
        }
    )
    resources = resources.model_copy(
        update={"items": resources.items + (Item(id="lamp", definition_id="sword", owner_id="a"),)}
    )
    build, definitions = approved()
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="bad-fuel",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ValidationError, match="exact consumable"):
        apply_general_equipment(
            resource_engine,
            after,
            UseGeneralEquipment(
                id="burn",
                actor_id="a",
                expected_revision=after.revision,
                item_id="lamp",
                consumable_item_id="device",
            ),
            actor_technology_level=8,
        )
    assert any(item.id == "device" for item in after.items)


@pytest.mark.parametrize("protocol", [False, True])
async def test_failed_crafted_ammunition_cannot_be_loaded_or_fired(
    tmp_path: Path, protocol: bool
) -> None:
    from test_firearm_malfunctions import firearm
    from test_gurps_melee import setup
    from test_gurps_ranged import scene, weapon

    from wayfarer.engine.rules.types.readiness import ProjectileReadiness
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.combat.ranged.ammunition import reload_weapon
    from wayfarer.engine.simulation.combat.ranged.equipment import ammunition_profile
    from wayfarer.engine.simulation.combat.ranged.readiness import reload
    from wayfarer.engine.simulation.resources import AmmunitionLoad
    from wayfarer.engine.simulation.traits.gizmos import BeginGizmoSession, begin_session

    mode = (
        firearm().model_copy(update={"readiness": ProjectileReadiness(kind="firearm")})
        if protocol
        else weapon()
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    state = play._load(await play.store.read(cid))
    resource_engine = play.engine.resources
    profile = ObjectProfile(construction="unliving", hp=5, dr=0, ht=10)
    resource_engine.specs["equipment:ammo"] = resource_engine.specs["equipment:ammo"].model_copy(
        update={"durability": profile}
    )
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"quantity": 1, "condition": ObjectCondition(hp=5)})
                if item.definition_id == "equipment:ammo"
                else item
                for item in state.resources.items
            )
        }
    )
    resources, _ = begin_session(
        resources,
        BeginGizmoSession(
            id="ammo-session", actor_id="gm", expected_revision=resources.revision, session_id="one"
        ),
        authorized_actor_id="gm",
        system=True,
    )
    approval = context()[2].model_copy(
        update={
            "item": Item(
                id="failed-ammo",
                definition_id="equipment:ammo",
                owner_id="a",
                condition=ObjectCondition(hp=5),
            ),
            "materials": (GizmoMaterial(item_id="ammo-a", quantity=1),),
        }
    )
    build, definitions = approved()
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="make-ammo",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    candidate = state.model_copy(update={"resources": after, "revision": after.revision})
    command = TakeCombatTurn(
        id="load-failed",
        actor_id="a",
        expected_revision=after.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        reload_ammunition_id="failed-ammo",
    )
    with pytest.raises(ValidationError, match="owned weapon and ammunition"):
        reload_weapon(play.rules_context, candidate, command)
    if protocol:
        with pytest.raises(ValidationError, match="Broken"):
            reload(play.rules_context, candidate, command, mode, validate_only=False)
    loaded = after.model_copy(
        update={
            "ammunition_loads": (
                AmmunitionLoad(
                    weapon_id="sword-a",
                    mode_id="ranged",
                    ammunition_item_id="failed-ammo",
                    rounds=1,
                ),
            )
        }
    )
    equipment = play.engine.rules.combat
    assert equipment is not None and equipment.gurps_equipment is not None
    with pytest.raises(ValidationError, match="disabled"):
        ammunition_profile(equipment.gurps_equipment, loaded, "sword-a", mode)


async def test_failed_device_keeps_custody_and_can_be_repaired(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.engine.simulation.equipment.repair_transitions import repair
    from wayfarer.engine.simulation.resources import Transfer, Unequip
    from wayfarer.engine.simulation.traits.gizmos import BeginGizmoSession, begin_session

    profile = ObjectProfile(
        construction="unliving",
        hp=5,
        dr=0,
        ht=10,
        repair_skill_id="skill:carpentry",
        repair_tools_definition="equipment:tools",
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=profile,
        start_encounter=False,
    )
    state = play._load(await play.store.read(cid))
    resources = play.engine.resources.apply(
        state.resources,
        Unequip(id="parts", actor_id="a", expected_revision=state.revision, item_id="sword-a"),
    )
    resources = play.engine.resources.apply(
        resources,
        Transfer(
            id="borrow-tools",
            actor_id="b",
            expected_revision=resources.revision,
            item_id="tool-b",
            quantity=1,
            owner_id="a",
        ),
    )
    resources, _ = begin_session(
        resources,
        BeginGizmoSession(
            id="repair-session",
            actor_id="gm",
            expected_revision=resources.revision,
            session_id="one",
        ),
        authorized_actor_id="gm",
        system=True,
    )
    approval = context()[2].model_copy(
        update={
            "item": Item(
                id="failed-device",
                definition_id="equipment:broadsword",
                owner_id="a",
                condition=ObjectCondition(hp=5),
            ),
            "materials": (GizmoMaterial(item_id="sword-a", quantity=1),),
        }
    )
    build, definitions = approved()
    failed, _ = craft_gizmo(
        play.engine.resources,
        resources,
        RevealGizmo(
            id="make-repairable",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    condition = next(item.condition for item in failed.items if item.id == "failed-device")
    assert condition is not None and condition.disabled
    transferred = play.engine.resources.apply(
        failed,
        Transfer(
            id="give-failed",
            actor_id="a",
            expected_revision=failed.revision,
            item_id="failed-device",
            quantity=1,
            owner_id="b",
        ),
    )
    assert (
        next(item for item in transferred.items if item.id == "failed-device").condition
        == condition
    )
    returned = play.engine.resources.apply(
        transferred,
        Transfer(
            id="return-failed",
            actor_id="b",
            expected_revision=transferred.revision,
            item_id="failed-device",
            quantity=1,
            owner_id="a",
        ),
    )
    candidate = state.model_copy(update={"resources": returned, "revision": returned.revision})
    started, task = repair(
        play.rules_context,
        candidate,
        actor_id="a",
        item_id="failed-device",
        command_id="repair-start",
        stage="start",
        task_id=None,
    )
    assert task.status == "pending" and task.condition == condition
    due = started.model_copy(
        update={"resources": started.resources.model_copy(update={"game_time": task.due})}
    )
    play.rng = RecordedDice((1, 2, 3))
    repaired, result = repair(
        play.rules_context,
        due,
        actor_id="a",
        item_id="failed-device",
        command_id="repair-finish",
        stage="finish",
        task_id=task.id,
    )
    assert (
        result.status == "completed" and result.check is not None and result.check.outcome.succeeded
    )
    actual = next(item for item in repaired.resources.items if item.id == "failed-device")
    assert actual.owner_id == "a" and actual.condition is not None and not actual.condition.disabled
    assert len(gm_gizmo_rolls(repaired.resources, gm_authorized=True)) == 1


def test_carried_container_materials_are_available_without_minting_supplies() -> None:
    resource_engine, resources, approval = context()
    resources = resources.model_copy(
        update={
            "items": (
                resources.items[0].model_copy(update={"container_id": "kit"}),
                Item(id="kit", definition_id="bag", owner_id="a", condition=ObjectCondition(hp=5)),
            )
        }
    )
    build, definitions = approved()
    after, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="kit-materials",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((1, 2, 3)),
        authorized_actor_id="a",
        system=True,
    )
    assert next(item for item in after.items if item.id == "raw").quantity == 1
    assert next(item for item in after.items if item.id == "raw").container_id == "kit"
    check = gm_gizmo_rolls(after, gm_authorized=True)[0].check
    assert check is not None and check.base_target == 10
    assert check.modifiers[0].value == -2 and check.effective_target == 8


def test_consumed_invention_identity_cannot_be_recreated_next_session() -> None:
    from wayfarer.engine.simulation.resources import Consume
    from wayfarer.engine.simulation.traits.gizmos import (
        BeginGizmoSession,
        GizmoEligibility,
        begin_session,
        reveal_gizmo,
    )

    resource_engine, resources, approval = context()
    build, definitions = approved()
    made, _ = craft_gizmo(
        resource_engine,
        resources,
        RevealGizmo(
            id="first-instance",
            actor_id="a",
            expected_revision=resources.revision,
            session_id="one",
            eligibility_id="craft",
        ),
        build,
        definitions,
        approval,
        rng=RecordedDice((4, 4, 4)),
        authorized_actor_id="a",
        system=True,
    )
    # A nonworking object can still be removed through ordinary inventory custody.
    removed = resource_engine.apply(
        made,
        Consume(
            id="discard-device",
            actor_id="a",
            expected_revision=made.revision,
            item_id="device",
            quantity=1,
        ),
    )
    assert all(item.id != "device" for item in removed.items + removed.expended_items)
    next_session, _ = begin_session(
        removed,
        BeginGizmoSession(
            id="next-session", actor_id="gm", expected_revision=removed.revision, session_id="two"
        ),
        authorized_actor_id="gm",
        system=True,
    )
    command = RevealGizmo(
        id="recycle-identity",
        actor_id="a",
        expected_revision=next_session.revision,
        session_id="two",
        eligibility_id="fresh-approval",
    )
    with pytest.raises(ConflictError, match="instance has already been used"):
        craft_gizmo(
            resource_engine,
            next_session,
            command,
            build,
            definitions,
            approval.model_copy(update={"id": "fresh-approval", "session_id": "two"}),
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ConflictError, match="instance or eligibility"):
        reveal_gizmo(
            resource_engine,
            next_session,
            command,
            build,
            definitions,
            (
                GizmoEligibility(
                    id="fresh-approval",
                    actor_id="a",
                    session_id="two",
                    item=approval.item,
                    category="common-device",
                    pocket_sized=True,
                    could_have_carried=True,
                    inexpensive=True,
                    widely_available_at_actor_tl=True,
                ),
            ),
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize("potential_die", [1, 3])
def test_sentient_recipe_rejects_before_success_or_failure_but_existing_revelation_is_valid(
    potential_die: int,
) -> None:
    from test_actions import Dice

    resource_engine, resources, approval = context()
    profile = resource_engine.specs["bag"].durability
    assert profile is not None
    resource_engine.specs["bag"] = resource_engine.specs["bag"].model_copy(
        update={"durability": profile.model_copy(update={"sentient": True})}
    )
    resources = resources.model_copy(
        update={
            "pools": resources.pools
            + (
                Pool(
                    id="hp:b",
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
                ),
            )
        }
    )
    item = approval.item.model_copy(update={"condition": None, "machine_actor_id": "b"})
    approval = approval.model_copy(update={"item": item})
    build, definitions = approved()
    command = RevealGizmo(
        id="sentient",
        actor_id="a",
        expected_revision=resources.revision,
        session_id="one",
        eligibility_id="craft",
    )
    rng = Dice(potential_die)
    before = resources.model_dump_json()
    with pytest.raises(ValidationError, match="nonsentient object condition"):
        craft_gizmo(
            resource_engine,
            resources,
            command,
            build,
            definitions,
            approval,
            rng=rng,
            authorized_actor_id="a",
            system=True,
        )
    assert rng.calls == 0 and resources.model_dump_json() == before
    revealed, result = craft_gizmo(
        resource_engine,
        resources,
        command,
        build,
        definitions,
        approval.model_copy(update={"build_on_spot": False}),
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    )
    assert revealed.items[-1] == item and revealed.pools == resources.pools
    assert result.uses_remaining == 0
    assert gm_gizmo_rolls(revealed, gm_authorized=True)[0].check is None
