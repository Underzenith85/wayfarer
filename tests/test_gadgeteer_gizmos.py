"""B58 materials, failed devices, actual backfire and private deterministic receipts."""

from dataclasses import replace

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
    GizmoPrivateState,
    craft_gizmo,
    gm_gizmo_rolls,
)
from wayfarer.engine.simulation.traits.gizmos import RevealGizmo
from wayfarer.errors import ConflictError, ValidationError


def approved() -> tuple[ValidatedBuild, dict[str, RuleDefinition]]:
    base = runtime_compiler()
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
    package = replace(package, definitions=package.definitions + (skill,))
    rules = replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),))
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        base.policy,
        statistics_profile="gurps-basic-set-4e-2004",
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
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
    after, private, outcome = craft_gizmo(
        resource_engine,
        before,
        GizmoPrivateState(),
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
        gm_gizmo_rolls(private)
    rolls = gm_gizmo_rolls(private, gm_authorized=True)
    assert rolls[0].check is not None and rolls[0].check.dice == dice
    assert rolls[0].check.effective_target == 8
    assert rolls[0].hp_lost == 10 - hp
    restored = ResourceState.model_validate_json(after.model_dump_json())
    restored_private = GizmoPrivateState.model_validate_json(private.model_dump_json())
    assert craft_gizmo(
        resource_engine,
        restored,
        restored_private,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    ) == (restored, restored_private, outcome)
    with pytest.raises(ValidationError, match="authority"):
        craft_gizmo(
            resource_engine,
            restored,
            restored_private,
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
                GizmoPrivateState(),
                command,
                build,
                definitions,
                invalid,
                rng=RecordedDice(()),
                authorized_actor_id="a",
                system=True,
            )
    after, private, _ = craft_gizmo(
        resource_engine,
        resources,
        GizmoPrivateState(),
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
            private,
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
            private,
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
    after, private, outcome = craft_gizmo(
        resource_engine,
        resources,
        GizmoPrivateState(),
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    )
    assert after.items[0].quantity == 3 and after.items[1] == item
    assert outcome.uses_remaining == 0 and private.rolls[0].check is None


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
    after, private, outcome = craft_gizmo(
        resource_engine,
        resources,
        GizmoPrivateState(),
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
        private,
        command,
        build,
        definitions,
        approval,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
    ) == (after, private, outcome)
