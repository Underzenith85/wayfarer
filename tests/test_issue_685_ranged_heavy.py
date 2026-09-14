"""Executable source evidence for issues #685, #702, and #703."""

from decimal import Decimal

from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.simulation.combat.ranged.equipment import (
    ammunition_matches,
    effective_mode,
    holdout_modifier,
    persist_back_blast,
    persist_follow_up,
    persist_surge,
    resolve_follow_up,
    surge_disruption,
)
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import Item, Pool, ResourceState

ENTRIES = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}


def test_b281_has_every_weapon_and_a_typed_load() -> None:
    identifiers = {
        "atgm-115mm",
        "sam-70mm",
        "scorpion",
        "hmg-50",
        "under-barrel-40mm",
        "integral-25mm",
        "bazooka-60mm",
        "rpg-85mm",
        "law-84mm",
        "auto-rifle-762",
        "lmg-762",
        "saw-556",
        "flamethrower",
    }
    rows = {
        entry.definition_id.removeprefix("equipment:"): entry
        for entry in ENTRIES.values()
        if entry.provenance.pages == (281,) and entry.modes
    }
    assert set(rows) == identifiers
    for entry in rows.values():
        assert not entry.unsupported_mechanics
        mode = entry.modes[0]
        assert isinstance(mode, RangedMode) and mode.ammunition_id in ENTRIES
        ammunition = ENTRIES[mode.ammunition_id]
        assert ammunition.ammunition and ammunition.provenance.pages == (281,)


def test_heavy_footnotes_bind_to_existing_procedures() -> None:
    atgm = ENTRIES["equipment:atgm-115mm"].modes[0]
    sam = ENTRIES["equipment:sam-70mm"].modes[0]
    hmg = ENTRIES["equipment:hmg-50"].modes[0]
    under = ENTRIES["equipment:under-barrel-40mm"].modes[0]
    integral = ENTRIES["equipment:integral-25mm"].modes[0]
    flame = ENTRIES["equipment:flamethrower"].modes[0]
    assert isinstance(atgm, RangedMode) and isinstance(sam, RangedMode)
    assert atgm.back_blast is not None and atgm.guidance is not None
    assert (atgm.minimum_range, atgm.back_blast.range_yards, atgm.guidance.kind) == (
        30,
        30,
        "guided",
    )
    assert not atgm.loses_armor_divisor_at_half_range
    assert sam.guidance is not None
    assert (sam.minimum_range, sam.guidance.kind, sam.guidance.seeker_sense) == (
        200,
        "homing",
        "hyperspectral-vision",
    )
    assert isinstance(hmg, RangedMode) and hmg.mount and hmg.minimum_shots_per_attack == 8
    assert hmg.mount.required_mount_definition_id == "equipment:hmg-50-tripod"
    assert ENTRIES["equipment:hmg-50-tripod"].weight_millipounds == 44000
    assert isinstance(under, RangedMode) and under.attachment and under.attachment.inherit_bulk
    assert under.attachment.host_skill_ids == ("skill:guns-rifle",)
    assert isinstance(integral, RangedMode) and integral.attachment
    assert integral.attachment.host_definition_id == "equipment:icw-68mm"
    assert isinstance(flame, RangedMode) and flame.sprayer
    assert (
        flame.sprayer.sustained_seconds,
        flame.sprayer.rounds_per_second,
        flame.sprayer.ignites,
    ) == (10, 1, True)
    law = ENTRIES["equipment:law-84mm"].modes[0]
    assert isinstance(law, RangedMode) and law.firearm
    assert law.firearm.action == "single-use"


def test_explosive_loads_preserve_multiplier_divisor_and_fragmentation() -> None:
    atgm = ENTRIES["equipment:atgm-115mm-missile"].warhead
    sam = ENTRIES["equipment:sam-70mm-missile"].warhead
    grenade = ENTRIES["equipment:grenade-40mm"].warhead
    assert atgm and (atgm.dice, atgm.multiplier, atgm.armor_divisor) == (6, 10, Decimal(10))
    assert sam and (sam.dice, sam.multiplier, sam.fragmentation_dice) == (6, 3, 6)
    assert grenade and (grenade.dice, grenade.armor_divisor, grenade.fragmentation_dice) == (
        4,
        Decimal(10),
        2,
    )


def test_ammunition_variants_are_inventory_choices_and_change_only_authored_facts() -> None:
    pistol = ENTRIES["equipment:auto-pistol-9mm-tl7"].modes[0]
    hp = ENTRIES["equipment:auto-pistol-9mm-tl7-round-hp"]
    aphc = ENTRIES["equipment:auto-pistol-9mm-tl7-round-aphc"]
    apds = ENTRIES["equipment:auto-pistol-9mm-tl7-round-apds"]
    assert isinstance(pistol, RangedMode)
    assert all(ammunition_matches(pistol, profile) for profile in (hp, aphc, apds))
    assert (
        effective_mode(pistol, hp).damage.damage_type,
        effective_mode(pistol, hp).damage.armor_divisor,
    ) == ("pi+", Decimal("0.5"))
    sabot = effective_mode(pistol, apds)
    assert (
        sabot.damage.damage_type,
        sabot.damage.armor_divisor,
        sabot.damage.adds,
        sabot.half_damage_range,
        sabot.maximum_range,
    ) == ("pi-", Decimal(2), 4, Decimal(225), Decimal(2775))
    assert aphc.price == ENTRIES["equipment:auto-pistol-9mm-tl7-round"].price * 2
    variants = [entry for entry in ENTRIES.values() if entry.ammunition_variant]
    # 93 firearm choices plus arrow, bolt, lead sling, and tranquilizer variants.
    assert len(variants) == 97
    assert not any("shotgun" in entry.definition_id for entry in variants)
    assert not any("gauss" in entry.definition_id for entry in variants)
    assert not any("dart-rifle" in entry.definition_id for entry in variants)


def test_bodkins_lead_shot_and_bulk_holdout_are_explicit() -> None:
    bow = ENTRIES["equipment:longbow"].modes[0]
    bodkin = ENTRIES["equipment:arrow-bodkin"]
    sling = ENTRIES["equipment:sling"].modes[0]
    lead = ENTRIES["equipment:sling-lead-bullet"]
    assert isinstance(bow, RangedMode) and isinstance(sling, RangedMode)
    assert effective_mode(bow, bodkin).damage.model_dump()["damage_type"] == "pi"
    lead_mode = effective_mode(sling, lead)
    assert (lead_mode.damage.adds, lead_mode.maximum_range) == (1, Decimal(20))
    pistol = ENTRIES["equipment:holdout-pistol-380"].modes[0]
    assert isinstance(pistol, RangedMode)
    assert holdout_modifier(pistol) == -1


def test_penetrating_tranquilizer_and_linked_electrical_effects_are_resolved() -> None:
    tranquilizer = FollowUpSpec(
        payload_id="drug:tranquilizer",
        kind="drug",
        resistance_penalty=-3,
        condition="unconsciousness",
        duration_minutes_per_margin=1,
    )
    stopped = resolve_follow_up(
        tranquilizer, penetrated_damage=0, target_ht=10, resistance_dice=(6, 6, 6)
    )
    failed = resolve_follow_up(
        tranquilizer, penetrated_damage=1, target_ht=10, resistance_dice=(4, 4, 4)
    )
    assert not stopped.applied and stopped.reason == "carrier-stopped"
    assert (
        failed.applied and failed.reason == "failed-resistance" and failed.duration_seconds == 300
    )
    electrolaser = ENTRIES["equipment:electrolaser-pistol"].modes[0]
    assert isinstance(electrolaser, RangedMode) and electrolaser.linked_follow_up
    assert not electrolaser.linked_follow_up.requires_penetration
    assert electrolaser.damage.surge and electrolaser.requires_atmosphere
    assert surge_disruption(surge=True, target_is_electrical=True, penetrating_damage=1)
    assert not surge_disruption(surge=True, target_is_electrical=False, penetrating_damage=1)


def test_follow_up_and_surge_results_persist_and_mutate_authoritative_state() -> None:
    injury = InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human")
    resources = ResourceState(
        pools=(Pool(id="hp:target", current=10, maximum=10, injury=injury),),
        items=(
            Item(
                id="radio",
                definition_id="equipment:radio",
                owner_id="target",
                condition=ObjectCondition(hp=5),
            ),
        ),
    )
    spec = FollowUpSpec(
        payload_id="drug:tranquilizer",
        kind="drug",
        resistance_penalty=-3,
        condition="unconsciousness",
        duration_minutes_per_margin=1,
    )
    result = resolve_follow_up(spec, penetrated_damage=1, target_ht=10, resistance_dice=(4, 4, 4))
    followed = persist_follow_up(
        resources,
        event_id="shot:follow-up:0",
        target_actor_id="target",
        spec=spec,
        result=result,
    )
    target = next(pool for pool in followed.pools if pool.id == "hp:target")
    assert target.injury and target.injury.unconscious
    assert followed.events[-1].id == "shot:follow-up:0"
    assert followed.afflictions[-1].source_id == "drug:tranquilizer"
    assert (
        persist_follow_up(
            followed,
            event_id="shot:follow-up:0",
            target_actor_id="target",
            spec=spec,
            result=result,
        )
        == followed
    )

    surged = persist_surge(
        followed,
        event_id="shot:surge:0",
        target_item_id="radio",
        penetrating_damage=1,
        target_is_electrical=True,
    )
    radio = next(item for item in surged.items if item.id == "radio")
    assert radio.condition and radio.condition.disabled
    assert surged.events[-1].kind == "surge-disruption-v1"

    law = ENTRIES["equipment:law-84mm"].modes[0]
    assert isinstance(law, RangedMode)
    back_blast = persist_back_blast(
        surged,
        event_id="shot:back-blast",
        weapon_item_id="law",
        mode=law,
        shots_fired=1,
    )
    assert '"kind":"back-blast-v1"' in back_blast.events[-1].kind
