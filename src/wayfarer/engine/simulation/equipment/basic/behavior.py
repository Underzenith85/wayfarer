"""Executable B288-B289 behavior joined to every formerly blocked row."""

from __future__ import annotations

from wayfarer.engine.rules.types.equipment import AccessorySpec, EquipmentUseSpec, FuelSpec
from wayfarer.engine.simulation.equipment.basic.gear import ORDINARY
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile


def use(
    identifier: str,
    kind: str,
    *,
    skills: tuple[str, ...] = (),
    modifier: int = 0,
    duration: int | None = None,
    capacity: int | None = None,
    range_yards: int | None = None,
    protection: int | None = None,
    consumes: str | None = None,
    minimum_tl: int | None = None,
) -> EquipmentUseSpec:
    return EquipmentUseSpec(
        id=identifier,
        kind=kind,  # type: ignore[arg-type]
        skill_ids=skills,
        modifier=modifier,
        duration_seconds=duration,
        capacity=capacity,
        range_yards=range_yards,
        protection=protection,
        consumes_definition_id=consumes,
        minimum_technology_level=minimum_tl,
    )


# Every entry is an explicit source-facing adapter. Shared helpers do not infer
# behavior from an item's name, TL, price, or category.
USES: dict[str, tuple[EquipmentUseSpec, ...]] = {
    "steel-cable-15": (use("support", "capacity", capacity=3_700),),
    "cigarette-lighter": (use("light-fire", "fire"),),
    "climbing-gear": (use("climb", "basic-equipment", skills=("skill:climbing",)),),
    "compass": (use("navigate", "skill-bonus", skills=("skill:navigation",), modifier=1),),
    "cord-3-16": (use("support", "capacity", capacity=90),),
    "fishhooks-and-line": (use("fish", "basic-equipment", skills=("skill:fishing",)),),
    "gps-receiver": (
        use(
            "navigate",
            "skill-bonus",
            skills=("skill:navigation",),
            modifier=3,
            consumes="equipment:battery",
        ),
    ),
    "grapnel": (use("anchor-climb", "basic-equipment", skills=("skill:climbing",)),),
    "group-basics": (
        use("cook", "basic-equipment", skills=("skill:cooking",)),
        use("survive", "basic-equipment", skills=("skill:survival",)),
    ),
    "iron-spike": (use("anchor-climb", "basic-equipment", skills=("skill:climbing",)),),
    "life-jacket": (use("float", "protection", protection=1),),
    "waterproof-matches-50": (use("light-fire", "fire"),),
    "parachute": (use("controlled-descent", "mobility", skills=("skill:parachuting",)),),
    "personal-basics": (use("survive", "skill-bonus", skills=("skill:survival",), modifier=2),),
    "rope-3-8": (use("support", "capacity", capacity=300),),
    "rope-3-4": (use("support", "capacity", capacity=1_100),),
    "scuba-gear": (use("dive", "basic-equipment", skills=("skill:scuba",), duration=7_200),),
    "insulated-sleeping-bag": (use("resist-freezing", "rest", modifier=3),),
    "hard-suitcase": (use("locked-storage", "protection", capacity=100, protection=4),),
    "thermos-bottle": (use("retain-temperature", "protection", duration=86_400, capacity=1),),
    "water-purification-tablets-50": (use("purify-water", "purify", capacity=50),),
    "drum": (use("signal", "communication", range_yards=5_280),),
    "mini-recorder": (use("record-audio", "record", duration=10_800),),
    "digital-mini-recorder": (use("record-audio", "record", consumes="equipment:battery"),),
    "transistor-radio": (
        use("receive-radio", "communication", duration=28_800, consumes="equipment:battery"),
    ),
    "mini-tv": (
        use("receive-video", "communication", duration=14_400, consumes="equipment:battery"),
    ),
    "bit-and-bridle": (
        use("control-mount-one-hand", "control-bonus", modifier=2),
        use("control-mount-two-hands", "control-bonus", modifier=3),
    ),
    "horseshoes": (use("long-ride-stamina", "skill-bonus", skills=("attribute:ht",), modifier=2),),
    "saddle-and-tack": (use("ride", "basic-equipment", skills=("skill:riding",)),),
    "spurs": (use("control-mount", "control-bonus", modifier=1),),
    "stirrups": (
        use("mount-and-control", "control-bonus", modifier=1),
        use("lance-prerequisite", "basic-equipment", skills=("skill:lance",)),
    ),
    "war-saddle": (
        use("remain-seated", "skill-bonus", skills=("skill:riding",), modifier=1),
        use("unconscious-seat", "protection", protection=50),
    ),
    "audio-bug": (
        use("transmit-bug", "communication", duration=604_800, range_yards=440),
        use("conceal-bug", "sense-bonus", modifier=-7),
    ),
    "bug-stomper": (use("jam-bugs", "communication", duration=28_800, range_yards=10),),
    "disguise-kit": (use("disguise", "skill-bonus", skills=("skill:disguise",), modifier=1),),
    "electronic-lockpicks": (
        use("pick-electronic-lock", "skill-bonus", skills=("skill:lockpicking",), modifier=2),
    ),
    "handcuffs": (use("restrain", "restraint", skills=("skill:escape",), modifier=-5),),
    "homing-beacon": (use("track-beacon", "sense-bonus", duration=43_200, range_yards=1_760),),
    "laser-microphone": (use("eavesdrop-glass", "sense-bonus", range_yards=300),),
    "lockpicks": (use("pick-lock", "basic-equipment", skills=("skill:lockpicking",)),),
    "nanobug": (use("record-bug", "record"), use("conceal-bug", "sense-bonus", modifier=-10)),
    "shotgun-microphone": (use("listen", "sense-bonus", modifier=1),),
    "binoculars": (use("telescopic-vision", "sense-bonus", modifier=2),),
    "camcorder": (
        use("record-video", "record", duration=25_200),
        use("night-vision", "sense-bonus", modifier=5),
    ),
    "camera-35mm": (
        use(
            "photograph",
            "basic-equipment",
            skills=("skill:photography",),
            consumes="equipment:camera-film-32",
        ),
    ),
    "digital-mini-camera": (use("photograph", "record"),),
    "spy-camera": (use("photograph", "record", capacity=36),),
    "telescope": (use("telescopic-vision", "sense-bonus", modifier=1),),
    "balance-and-weights": (use("weigh-goods", "basic-equipment", skills=("skill:merchant",)),),
    "three-foot-crowbar": (use("pry", "basic-equipment", skills=("attribute:st",)),),
    "cutting-torch": (
        use("cut", "maintenance", duration=1, consumes="equipment:cutting-torch-gas-bottle"),
    ),
    "pickaxe": (use("dig", "production", skills=("skill:digging",), modifier=1),),
    "iron-plow": (use("plow-rough-soil", "production"),),
    "wooden-plow": (use("plow", "production"),),
    "saw": (use("fell-timber", "production", skills=("skill:woodcutting",)),),
    "shovel": (use("dig", "production", skills=("skill:digging",), modifier=1),),
    "spinning-wheel": (use("spin-yarn", "production", modifier=6),),
    "wheelbarrow": (use("carry-load", "capacity", capacity=350, modifier=5),),
    "whetstone": (use("sharpen", "maintenance"),),
    "bandages": (use("first-aid", "basic-equipment", skills=("skill:first-aid",)),),
    "crash-kit": (
        use("first-aid", "skill-bonus", skills=("skill:first-aid",), modifier=2),
        use("surgery", "skill-bonus", skills=("skill:surgery",), modifier=-5, minimum_tl=6),
    ),
    "first-aid-kit": (use("first-aid", "skill-bonus", skills=("skill:first-aid",), modifier=1),),
    "surgical-instruments": (use("surgery", "basic-equipment", skills=("skill:surgery",)),),
    "portable-carpentry-tool-kit": (
        use("carpentry", "basic-equipment", skills=("skill:carpentry",)),
    ),
    "portable-armoury-tool-kit": (use("armoury", "basic-equipment", skills=("skill:armoury",)),),
    "portable-explosives-tool-kit": (
        use("explosives", "basic-equipment", skills=("skill:explosives",)),
    ),
    "portable-machinist-tool-kit": (
        use("machinist", "basic-equipment", skills=("skill:machinist",)),
    ),
    "portable-mechanic-tool-kit": (use("mechanic", "basic-equipment", skills=("skill:mechanic",)),),
    "portable-electrician-tool-kit": (
        use("electrician", "basic-equipment", skills=("skill:electrician",)),
    ),
    "portable-electronics-repair-tool-kit": (
        use("electronics-repair", "basic-equipment", skills=("skill:electronics-repair",)),
    ),
    "suitcase-lab": (use("scientific-lab", "basic-equipment", skills=("skill:science",)),),
}

FUELS = {
    "camp-stove": FuelSpec(
        kind="appliance", fuel_id="equipment:kerosene-gallon", seconds_per_charge=14_400
    ),
    "tallow-candle": FuelSpec(kind="supply", seconds_per_charge=43_200),
    "gasoline-gallon": FuelSpec(kind="supply", seconds_per_charge=1),
    "kerosene-gallon": FuelSpec(kind="supply", seconds_per_charge=14_400, initial_charges=4),
    "lantern": FuelSpec(
        kind="appliance", fuel_id="equipment:lantern-oil-pint", seconds_per_charge=86_400
    ),
    "lantern-oil-pint": FuelSpec(kind="supply", seconds_per_charge=86_400),
    "torch": FuelSpec(kind="supply", seconds_per_charge=3_600),
    "cutting-torch-gas-bottle": FuelSpec(kind="supply", seconds_per_charge=30),
    "heavy-flashlight": FuelSpec(
        kind="appliance", fuel_id="equipment:battery", seconds_per_charge=18_000
    ),
    "mini-flashlight": FuelSpec(
        kind="appliance", fuel_id="equipment:battery", seconds_per_charge=3_600
    ),
    "battery": FuelSpec(kind="supply", seconds_per_charge=1),
}

ACCESSORIES = {
    "ear-muffs": AccessorySpec(kind="hearing-protection", compatible="actor", hearing_modifier=5),
    "hip-quiver": AccessorySpec(kind="quiver", compatible="actor", capacity=20),
    "belt-holster": AccessorySpec(kind="holster", compatible="pistol"),
    "shoulder-holster": AccessorySpec(
        kind="holster", compatible="pistol", holdout_modifier=0, fast_draw_modifier=-1
    ),
    "leather-lanyard": AccessorySpec(
        kind="lanyard", compatible="ranged-weapon", cut_dr=2, cut_hp=2
    ),
    "woven-steel-lanyard": AccessorySpec(
        kind="lanyard", compatible="ranged-weapon", cut_dr=6, cut_hp=4
    ),
    "laser-sight": AccessorySpec(
        kind="laser-sight",
        compatible="ranged-weapon",
        attack_bonus=1,
        dodge_bonus_to_visible_target=1,
        powered_seconds=21_600,
    ),
    "scope-4x": AccessorySpec(
        kind="scope", compatible="ranged-weapon", accuracy_bonus=2, minimum_aim_seconds=2
    ),
    "thermal-scope-4x": AccessorySpec(
        kind="scope",
        compatible="ranged-weapon",
        accuracy_bonus=2,
        minimum_aim_seconds=2,
        grants_infravision=True,
        powered_seconds=7_200,
    ),
    "shoulder-quiver": AccessorySpec(kind="quiver", compatible="actor", capacity=12),
    "pistol-smg-silencer": AccessorySpec(
        kind="silencer", compatible="pistol-or-smg", hearing_modifier=-4
    ),
    "web-gear": AccessorySpec(kind="load-bearing", compatible="actor", capacity=1),
}


def _completed(row: EquipmentProfile) -> EquipmentProfile:
    identifier = row.definition_id.removeprefix("equipment:")
    uses = USES.get(identifier, ())
    fuel = FUELS.get(identifier)
    if fuel is not None and not uses:
        uses = (
            use(
                "operate" if fuel.kind == "appliance" else "consume",
                "light"
                if identifier not in {"gasoline-gallon", "kerosene-gallon", "battery"}
                else "fire",
                duration=fuel.seconds_per_charge,
            ),
        )
    accessory = ACCESSORIES.get(identifier)
    completed = bool(uses or fuel or accessory)
    blockers = tuple(
        blocker
        for blocker in row.unsupported_mechanics
        if not (
            completed
            and blocker
            in {
                "special-tool-effects",
                "fuel-consumption",
                "cold-resistance-bonus",
                "battery-and-computer-operation",
                "technology-level-variants",
                "weapon-accessories",
            }
        )
    )
    return row.model_copy(
        update={
            "uses": uses,
            "fuel": fuel,
            "accessory": accessory,
            "skill_relative_technology": row.technology_level == "skill-relative",
            "unsupported_mechanics": blockers,
        }
    )


GENERAL_EQUIPMENT = tuple(_completed(row) for row in ORDINARY)
