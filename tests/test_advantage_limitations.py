"""Independent executable fixtures for issue #683's B110-B117 limitations."""

import json
from pathlib import Path

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import (
    AREA,
    BREAKABLE,
    RAPID_FIRE,
    STOLEN,
    UNIQUE,
    AttackProfile,
    EnhancementParameters,
    GadgetConstruction,
    LimitationContext,
    LimitationParameters,
    ModifierApproval,
    ModifierSelection,
    apply_attack_modifiers,
    modified_cost,
    resolve_limitations,
)
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.equipment.gadget_limitations import (
    GadgetAbilityBinding,
    resolve_gadget_availability,
)
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, ResourceState
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def pick(
    identifier: str,
    *,
    level: int = 1,
    option: str | None = None,
    limitation: LimitationParameters | None = None,
    parameters: EnhancementParameters | None = None,
    gadget: GadgetConstruction | None = None,
) -> ModifierSelection:
    return ModifierSelection(
        definition_id=identifier,
        level=level,
        option=option,
        limitation=limitation,
        parameters=parameters,
        gadget=gadget,
    )


def test_source_fixture_binds_all_34_remaining_rows_to_typed_hooks() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/gurps/advantage-limitations.json").read_text())
    from wayfarer.engine.rules.traits.modifiers import MODIFIER_INDEX

    assert fixture["profile"] == "gurps-basic-set-4e-2004"
    assert len(fixture["rows"]) == 34
    for identifier, page, hook in fixture["rows"]:
        definition = MODIFIER_INDEX[identifier]
        assert (definition.page, definition.runtime_hook) == (page, hook)


def test_attack_limitations_project_exact_range_recoil_timing_and_damage_facts() -> None:
    selections = (
        pick(AREA),
        pick("modifier:limitation:dissipation"),
        pick(
            RAPID_FIRE,
            option="rof-4-7",
            parameters=EnhancementParameters(rate_of_fire=5),
        ),
        pick("modifier:limitation:extra-recoil", level=2),
        pick("modifier:limitation:reduced-range", level=2),
        pick("modifier:limitation:takes-extra-time", level=2),
        pick("modifier:limitation:takes-recharge", option="15-seconds"),
        pick("modifier:limitation:no-blunt-trauma-nbt"),
        pick("modifier:limitation:no-knockback-nkb"),
        pick("modifier:limitation:no-wounding-nw"),
    )
    changed = apply_attack_modifiers(
        AttackProfile(max_range=100, half_damage_range=20, activation_seconds=1),
        "innate-attack",
        selections,
    ).modified
    assert (changed.max_range, changed.half_damage_range) == (20, 4)
    assert (changed.recoil, changed.activation_seconds, changed.recharge_seconds) == (3, 4, 15)
    assert changed.dissipates_with_distance
    assert (changed.blunt_trauma_multiplier, changed.knockback_multiplier, changed.wounding) == (
        0,
        0,
        False,
    )


def test_access_preparation_trigger_recharge_fatigue_and_temporary_traits_execute() -> None:
    selections = (
        pick(
            "modifier:limitation:accessibility",
            option="moonlight",
            limitation=LimitationParameters(condition_id="condition:moonlight"),
        ),
        pick("modifier:limitation:costs-fatigue", level=2),
        pick(
            "modifier:limitation:preparation-required",
            option="1-minute",
            limitation=LimitationParameters(),
        ),
        pick("modifier:limitation:takes-recharge", option="5-seconds"),
        pick(
            "modifier:limitation:temporary-disadvantage",
            option="one-arm",
            limitation=LimitationParameters(temporary_disadvantage_ids=("disadvantage:one-arm",)),
        ),
        pick(
            "modifier:limitation:trigger",
            option="common",
            limitation=LimitationParameters(trigger_id="trigger:herb"),
        ),
    )
    approvals = (
        ModifierApproval(selections[0].definition_id, "moonlight", -20, frozenset({"advantage"})),
        ModifierApproval(selections[4].definition_id, "one-arm", -20, frozenset({"advantage"})),
    )
    receipt = resolve_limitations(
        AttackProfile(),
        "advantage",
        selections,
        LimitationContext(
            actor_id="hero",
            ability_id="flight",
            now=100,
            fp_available=2,
            satisfied_condition_ids=("condition:moonlight",),
            supplied_trigger_ids=("trigger:herb",),
            prepared_ability_id="flight",
            prepared_at=40,
        ),
        approvals,
    )
    assert receipt.available and receipt.fatigue_cost == 2
    assert receipt.next_available_at == 105
    assert receipt.temporary_disadvantage_ids == ("disadvantage:one-arm",)


def test_emergency_control_and_unreliable_checks_record_entropy() -> None:
    selections = (
        pick("modifier:limitation:uncontrollable", option="dangerous"),
        pick("modifier:limitation:unreliable", option="activation-11"),
    )
    receipt = resolve_limitations(
        AttackProfile(),
        "advantage",
        selections,
        LimitationContext(
            actor_id="hero", ability_id="storm", now=3, fp_available=0, emergency=True, will=10
        ),
        rng=RecordedDice((6, 6, 6, 1, 1, 1)),
    )
    assert receipt.available and not receipt.controlled
    assert tuple(trace.dice for trace in receipt.checks) == ((6, 6, 6), (1, 1, 1))


def test_gadget_availability_reads_exact_custody_and_durability_state() -> None:
    facts = GadgetConstruction(
        damage_resistance=5,
        size_modifier=-3,
        theft_method="quick-contest",
        unique=True,
    )
    selections = (pick(BREAKABLE, gadget=facts), pick(STOLEN, gadget=facts), pick(UNIQUE))
    binding = GadgetAbilityBinding(
        ability_id="luck", actor_id="hero", item_id="ring", definition_id="gadget:ring"
    )
    spec = EquipmentSpec(
        definition_id="gadget:ring",
        unit_weight=1,
        durability=ObjectProfile(construction="homogenous", hp=4, dr=5, ht=12, size_modifier=-3),
    )
    item = Item(
        id="ring",
        definition_id="gadget:ring",
        owner_id="hero",
        condition=ObjectCondition(hp=4),
    )
    state = ResourceState(revision=7, items=(item,))
    assert resolve_gadget_availability(
        state, binding, selections, {spec.definition_id: spec}
    ).available
    stolen = state.model_copy(
        update={"revision": 8, "items": (item.model_copy(update={"owner_id": "thief"}),)}
    )
    result = resolve_gadget_availability(stolen, binding, selections, {spec.definition_id: spec})
    assert (result.revision, result.available, result.reason) == (8, False, "stolen")
    broken = state.model_copy(
        update={
            "revision": 9,
            "items": (item.model_copy(update={"condition": ObjectCondition(hp=0, disabled=True)}),),
        }
    )
    assert (
        resolve_gadget_availability(broken, binding, selections, {spec.definition_id: spec}).reason
        == "broken"
    )


def test_gadget_variants_require_only_their_source_supported_authority() -> None:
    facts = GadgetConstruction(
        damage_resistance=5,
        size_modifier=-3,
        theft_method="quick-contest",
        unique=False,
    )
    binding = GadgetAbilityBinding(
        ability_id="luck", actor_id="hero", item_id="ring", definition_id="gadget:ring"
    )
    ordinary = EquipmentSpec(definition_id="gadget:ring", unit_weight=1)
    ring = Item(id="ring", definition_id="gadget:ring", owner_id="hero")

    # Can Be Stolen does not silently acquire Breakable's durability requirements.
    stolen_only = (pick(STOLEN, gadget=facts),)
    held = ResourceState(revision=1, items=(ring,))
    assert resolve_gadget_availability(
        held, binding, stolen_only, {ordinary.definition_id: ordinary}
    ).available
    stolen = held.model_copy(
        update={"revision": 2, "items": (ring.model_copy(update={"owner_id": "thief"}),)}
    )
    assert (
        resolve_gadget_availability(
            stolen, binding, stolen_only, {ordinary.definition_id: ordinary}
        ).reason
        == "stolen"
    )
    reacquired = stolen.model_copy(
        update={"revision": 3, "items": (ring.model_copy(update={"owner_id": "hero"}),)}
    )
    assert resolve_gadget_availability(
        reacquired, binding, stolen_only, {ordinary.definition_id: ordinary}
    ).available

    # Breakable consults exact object state, while loss of a non-Unique exact item
    # remains unavailable until campaign authority rebinds or restores that item.
    durable = EquipmentSpec(
        definition_id="gadget:ring",
        unit_weight=1,
        durability=ObjectProfile(construction="homogenous", hp=4, dr=5, ht=12, size_modifier=-3),
    )
    disabled = ResourceState(
        revision=4,
        items=(ring.model_copy(update={"condition": ObjectCondition(hp=0, disabled=True)}),),
    )
    assert (
        resolve_gadget_availability(
            disabled, binding, (pick(BREAKABLE, gadget=facts),), {durable.definition_id: durable}
        ).reason
        == "broken"
    )
    repaired = disabled.model_copy(
        update={
            "revision": 5,
            "items": (ring.model_copy(update={"condition": ObjectCondition(hp=4)}),),
        }
    )
    assert resolve_gadget_availability(
        repaired, binding, (pick(BREAKABLE, gadget=facts),), {durable.definition_id: durable}
    ).available
    missing = ResourceState(revision=6)
    assert (
        resolve_gadget_availability(
            missing, binding, stolen_only, {ordinary.definition_id: ordinary}
        ).reason
        == "not-held"
    )


def test_missing_runtime_facts_and_invalid_source_relationships_fail_closed() -> None:
    with pytest.raises(ValidationError, match="authored condition"):
        resolve_limitations(
            AttackProfile(),
            "advantage",
            (pick("modifier:limitation:accessibility", option="night"),),
            LimitationContext(actor_id="hero", ability_id="flight", now=0),
            (
                ModifierApproval(
                    "modifier:limitation:accessibility", "night", -20, frozenset({"advantage"})
                ),
            ),
        )
    with pytest.raises(ValidationError, match="eligible delivery"):
        modified_cost(
            10,
            "innate-attack",
            (
                pick(
                    "modifier:limitation:resistible",
                    option="ht-5",
                    limitation=LimitationParameters(resistance_modifier=-5),
                ),
            ),
        )
