"""General equipment rows (B288-289)."""

from decimal import Decimal
from typing import cast

from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature as Feature
from wayfarer.engine.simulation.equipment.basic.rows import source
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, TechnologyLevel

# B288-289: fixed-TL inventory facts. Behavioral requirements keep special tools
# out of active packages until their mechanics are integrated; no invented
# skill/effect hooks. Rows printed with ``TL Var.`` remain ledger omissions
# because the catalog currently requires one concrete technology level.
ORDINARY = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(page),
        technology_level=cast(TechnologyLevel, tl),
        price=price,
        weight_millipounds=weight,
        container_capacity_millipounds=capacity,
        unsupported_mechanics=missing,
    )
    for identifier, page, tl, price, weight, capacity, missing in (
        # Camping and survival gear, B288.
        ("equipment:frame-backpack", 288, 1, 100, 10000, 100000, ()),
        ("equipment:small-backpack", 288, 1, 60, 3000, 40000, ()),
        ("equipment:blanket", 288, 1, 20, 4000, None, ()),
        ("equipment:ceramic-bottle", 288, 1, 3, 1000, 2000, ()),
        ("equipment:steel-cable-15", 288, 5, 100, 17000, None, ("special-tool-effects",)),
        ("equipment:camp-stove", 288, 6, 50, 2000, None, ("fuel-consumption",)),
        ("equipment:tallow-candle", 288, 1, 5, 1000, None, ("fuel-consumption",)),
        ("equipment:canteen", 288, 5, 10, 1000, 2000, ()),
        ("equipment:cigarette-lighter", 288, 6, 10, 0, None, ("special-tool-effects",)),
        ("equipment:climbing-gear", 288, 2, 20, 4000, None, ("special-tool-effects",)),
        ("equipment:compass", 288, 6, 50, 0, None, ("special-tool-effects",)),
        ("equipment:cord-3-16", 288, 0, 1, 500, None, ("special-tool-effects",)),
        ("equipment:fishhooks-and-line", 288, 0, 50, 0, None, ("special-tool-effects",)),
        ("equipment:heavy-flashlight", 288, 6, 20, 1000, None, ("battery-and-computer-operation",)),
        ("equipment:mini-flashlight", 288, 7, 10, 250, None, ("battery-and-computer-operation",)),
        ("equipment:gasoline-gallon", 288, 6, Decimal("1.5"), 6000, None, ("fuel-consumption",)),
        (
            "equipment:gps-receiver",
            288,
            8,
            200,
            3000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:grapnel", 288, 5, 20, 2000, None, ("special-tool-effects",)),
        ("equipment:group-basics", 288, 0, 50, 20000, None, ("special-tool-effects",)),
        ("equipment:iron-spike", 288, 2, 1, 500, None, ("special-tool-effects",)),
        ("equipment:kerosene-gallon", 288, 6, Decimal("1.5"), 6000, None, ("fuel-consumption",)),
        ("equipment:lantern", 288, 2, 20, 2000, None, ("fuel-consumption",)),
        ("equipment:life-jacket", 288, 6, 100, 6000, None, ("special-tool-effects",)),
        (
            "equipment:waterproof-matches-50",
            288,
            6,
            Decimal("1.5"),
            0,
            None,
            ("special-tool-effects",),
        ),
        ("equipment:lantern-oil-pint", 288, 2, 2, 1000, None, ("fuel-consumption",)),
        ("equipment:parachute", 288, 6, 1000, 30000, None, ("special-tool-effects",)),
        ("equipment:personal-basics", 288, 0, 5, 1000, None, ("special-tool-effects",)),
        ("equipment:six-foot-pole", 288, 0, 5, 3000, None, ()),
        ("equipment:ten-foot-pole", 288, 0, 8, 5000, None, ()),
        ("equipment:small-pouch", 288, 1, 10, 0, 3000, ()),
        ("equipment:rope-3-8", 288, 0, 5, 1500, None, ("special-tool-effects",)),
        ("equipment:rope-3-4", 288, 1, 25, 5000, None, ("special-tool-effects",)),
        ("equipment:scuba-gear", 288, 6, 1500, 32000, None, ("special-tool-effects",)),
        ("equipment:sleeping-bag", 288, 6, 25, 7000, None, ()),
        ("equipment:insulated-sleeping-bag", 288, 7, 100, 15000, None, ("cold-resistance-bonus",)),
        ("equipment:sleeping-fur", 288, 0, 50, 8000, None, ()),
        ("equipment:hard-suitcase", 288, 5, 250, 8000, 100000, ("special-tool-effects",)),
        ("equipment:one-man-tent", 288, 0, 50, 5000, None, ()),
        ("equipment:two-man-tent", 288, 0, 80, 12000, None, ()),
        ("equipment:four-man-tent", 288, 0, 150, 30000, None, ()),
        ("equipment:twenty-man-tent", 288, 1, 300, 100000, None, ()),
        ("equipment:thermos-bottle", 288, 5, 10, 2000, None, ("special-tool-effects",)),
        ("equipment:torch", 288, 0, 3, 1000, None, ("fuel-consumption",)),
        ("equipment:travelers-ration", 288, 0, 2, 500, None, ()),
        ("equipment:water-purification-tablets-50", 288, 6, 5, 0, None, ("special-tool-effects",)),
        ("equipment:wineskin", 288, 0, 10, 250, 8000, ()),
        ("equipment:wristwatch", 288, 6, 20, 0, None, ()),
        # Communications and information gear, B288-289.
        ("equipment:battery", 288, 6, 1, 0, None, ("battery-and-computer-operation",)),
        (
            "equipment:cell-phone",
            288,
            8,
            250,
            250,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:laptop", 288, 8, 1500, 3000, None, ("battery-and-computer-operation",)),
        (
            "equipment:wearable-computer",
            288,
            8,
            1000,
            2000,
            None,
            ("battery-and-computer-operation",),
        ),
        ("equipment:drum", 288, 0, 40, 2000, None, ("special-tool-effects",)),
        ("equipment:mini-recorder", 288, 7, 200, 500, None, ("battery-and-computer-operation",)),
        ("equipment:mini-recorder-tape", 288, 7, 5, 0, None, ()),
        (
            "equipment:digital-mini-recorder",
            288,
            8,
            30,
            500,
            None,
            ("battery-and-computer-operation",),
        ),
        (
            "equipment:backpack-radio",
            288,
            7,
            6000,
            15000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        (
            "equipment:hand-radio",
            288,
            7,
            100,
            1000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        (
            "equipment:headset-radio",
            288,
            8,
            500,
            500,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        (
            "equipment:secure-headset-radio",
            288,
            8,
            5000,
            500,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        (
            "equipment:satellite-phone",
            288,
            8,
            3000,
            3000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:scribes-kit", 288, 3, 50, 2000, None, ()),
        ("equipment:transistor-radio", 288, 7, 15, 500, None, ("battery-and-computer-operation",)),
        ("equipment:mini-tv", 288, 7, 150, 3000, None, ("battery-and-computer-operation",)),
        ("equipment:manual-typewriter", 289, 6, 200, 10000, None, ()),
        ("equipment:wax-tablet", 289, 1, 10, 2000, None, ()),
        # Equestrian gear, B289.
        ("equipment:bit-and-bridle", 289, 1, 35, 3000, None, ("special-tool-effects",)),
        ("equipment:horseshoes", 289, 3, 50, 4000, None, ("special-tool-effects",)),
        ("equipment:saddle-and-tack", 289, 2, 150, 15000, None, ("special-tool-effects",)),
        ("equipment:saddlebags", 289, 1, 100, 3000, 40000, ()),
        ("equipment:spurs", 289, 2, 25, 0, None, ("special-tool-effects",)),
        ("equipment:stirrups", 289, 3, 125, 20000, None, ("special-tool-effects",)),
        ("equipment:war-saddle", 289, 3, 250, 35000, None, ("special-tool-effects",)),
        # Law-enforcement, thief and spy gear, B289.
        (
            "equipment:audio-bug",
            289,
            7,
            200,
            0,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        (
            "equipment:bug-stomper",
            289,
            7,
            1200,
            2000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:disguise-kit", 289, 5, 200, 10000, None, ("special-tool-effects",)),
        ("equipment:electronic-lockpicks", 289, 7, 1500, 3000, None, ("special-tool-effects",)),
        ("equipment:handcuffs", 289, 5, 40, 500, None, ("special-tool-effects",)),
        (
            "equipment:homing-beacon",
            289,
            7,
            40,
            0,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:laser-microphone", 289, 8, 500, 2000, None, ("special-tool-effects",)),
        ("equipment:lockpicks", 289, 3, 50, 0, None, ("special-tool-effects",)),
        ("equipment:nanobug", 289, 8, 100, 0, None, ("special-tool-effects",)),
        ("equipment:shotgun-microphone", 289, 6, 250, 2000, None, ("special-tool-effects",)),
        # Optics and sensors, B289.
        ("equipment:binoculars", 289, 6, 400, 2000, None, ("special-tool-effects",)),
        (
            "equipment:camcorder",
            289,
            8,
            1000,
            1000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:camera-35mm", 289, 6, 50, 3000, None, ("special-tool-effects",)),
        ("equipment:camera-film-32", 289, 6, 10, 0, None, ()),
        (
            "equipment:metal-detector-wand",
            289,
            7,
            50,
            1000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:digital-mini-camera", 289, 8, 500, 0, None, ("special-tool-effects",)),
        (
            "equipment:night-vision-goggles",
            289,
            8,
            600,
            2000,
            None,
            ("battery-and-computer-operation", "special-tool-effects"),
        ),
        ("equipment:spy-camera", 289, 6, 500, 0, None, ("special-tool-effects",)),
        ("equipment:telescope", 289, 4, 500, 6000, None, ("special-tool-effects",)),
        # Fixed-TL tools, B289.
        ("equipment:balance-and-weights", 289, 1, 35, 3000, None, ("special-tool-effects",)),
        ("equipment:three-foot-crowbar", 289, 2, 20, 3000, None, ("special-tool-effects",)),
        ("equipment:cutting-torch", 289, 6, 500, 30000, None, ("special-tool-effects",)),
        ("equipment:cutting-torch-gas-bottle", 289, 6, 50, 15000, None, ("fuel-consumption",)),
        ("equipment:knitting-needles", 289, 3, 5, 0, None, ()),
        ("equipment:pickaxe", 289, 2, 15, 8000, None, ("special-tool-effects",)),
        ("equipment:iron-plow", 289, 2, 220, 120000, None, ("special-tool-effects",)),
        ("equipment:wooden-plow", 289, 1, 55, 60000, None, ("special-tool-effects",)),
        ("equipment:saw", 289, 0, 150, 3000, None, ("special-tool-effects",)),
        ("equipment:shovel", 289, 1, 12, 6000, None, ("special-tool-effects",)),
        ("equipment:spinning-wheel", 289, 3, 100, 40000, None, ("special-tool-effects",)),
        ("equipment:wheelbarrow", 289, 2, 60, 18000, 350000, ("special-tool-effects",)),
        ("equipment:whetstone", 289, 1, 5, 1000, None, ("special-tool-effects",)),
        # Skill-relative and portable specialist kits, B289.
        (
            "equipment:bandages",
            289,
            "skill-relative",
            10,
            2000,
            None,
            ("technology-level-variants", "special-tool-effects"),
        ),
        (
            "equipment:crash-kit",
            289,
            "skill-relative",
            200,
            10000,
            None,
            ("technology-level-variants", "special-tool-effects"),
        ),
        (
            "equipment:first-aid-kit",
            289,
            "skill-relative",
            50,
            2000,
            None,
            ("technology-level-variants", "special-tool-effects"),
        ),
        (
            "equipment:surgical-instruments",
            289,
            "skill-relative",
            300,
            15000,
            None,
            ("technology-level-variants", "special-tool-effects"),
        ),
        (
            "equipment:portable-carpentry-tool-kit",
            289,
            1,
            300,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-armoury-tool-kit",
            289,
            1,
            600,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-explosives-tool-kit",
            289,
            5,
            600,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-machinist-tool-kit",
            289,
            5,
            600,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-mechanic-tool-kit",
            289,
            5,
            600,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-electrician-tool-kit",
            289,
            6,
            600,
            20000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:portable-electronics-repair-tool-kit",
            289,
            6,
            1200,
            10000,
            None,
            ("special-tool-effects",),
        ),
        (
            "equipment:suitcase-lab",
            289,
            "skill-relative",
            3000,
            10000,
            None,
            ("technology-level-variants", "special-tool-effects"),
        ),
        # Weapon and combat accessories, B289.
        ("equipment:ear-muffs", 289, 6, 200, 1000, None, ("weapon-accessories",)),
        ("equipment:hip-quiver", 289, 0, 15, 1000, None, ("weapon-accessories",)),
        ("equipment:belt-holster", 289, 5, 25, 500, None, ("weapon-accessories",)),
        ("equipment:shoulder-holster", 289, 5, 50, 1000, None, ("weapon-accessories",)),
        ("equipment:leather-lanyard", 289, 0, 1, 0, None, ("weapon-accessories",)),
        ("equipment:woven-steel-lanyard", 289, 6, 15, 0, None, ("weapon-accessories",)),
        (
            "equipment:laser-sight",
            289,
            8,
            100,
            0,
            None,
            ("weapon-accessories", "battery-and-computer-operation"),
        ),
        ("equipment:scope-4x", 289, 6, 150, 1500, None, ("weapon-accessories",)),
        (
            "equipment:thermal-scope-4x",
            289,
            8,
            8000,
            4000,
            None,
            ("weapon-accessories", "battery-and-computer-operation"),
        ),
        ("equipment:shoulder-quiver", 289, 0, 10, 500, None, ("weapon-accessories",)),
        ("equipment:pistol-smg-silencer", 289, 6, 400, 1000, None, ("weapon-accessories",)),
        ("equipment:web-gear", 289, 6, 50, 2000, None, ("weapon-accessories",)),
    )
)


def _feature(
    kind: str,
    *,
    skill: str | None = None,
    modifier: int = 0,
    capacity: int | None = None,
    duration: int | None = None,
    distance: int | None = None,
    consumable: str | None = None,
    units: int = 0,
    target: str = "actor",
    trait: str | None = None,
    note: str = "",
    relative: bool = False,
    holdout: bool = False,
) -> Feature:
    return Feature(
        kind=kind,  # type: ignore[arg-type]
        skill_id=skill,
        modifier=modifier,
        capacity=capacity,
        duration_seconds=duration,
        range_yards=distance,
        consumable_definition_id=consumable,
        consumable_units=units,
        target=target,  # type: ignore[arg-type]
        trait_id=trait,
        note=note,
        technology_relative=relative,
        grants_holdout=holdout,
    )


# Every blocked B288-289 row receives the distinct source fact that makes it
# executable. Plain storage rows were already supported and do not appear here.
_FEATURES: dict[str, tuple[Feature, ...]] = {
    "steel-cable-15": (_feature("support", capacity=3700, note="10-yard cable"),),
    "camp-stove": (
        _feature(
            "fuel",
            duration=16 * 3600,
            consumable="equipment:kerosene-gallon",
            units=1,
            note="one gallon per sixteen hours (quarter-gallon per four hours)",
        ),
    ),
    "tallow-candle": (_feature("light", duration=12 * 3600, note="smoky candle"),),
    "cigarette-lighter": (_feature("tool", skill="skill:survival", note="starts fire"),),
    "climbing-gear": (_feature("tool", skill="skill:climbing", note="basic climbing equipment"),),
    "compass": (_feature("tool", skill="skill:navigation", modifier=1),),
    "cord-3-16": (_feature("support", capacity=90, note="10-yard cord"),),
    "fishhooks-and-line": (_feature("tool", skill="skill:fishing", note="requires a pole"),),
    "heavy-flashlight": (_feature("light", duration=5 * 3600, distance=10, note="30-foot beam"),),
    "mini-flashlight": (_feature("light", duration=3600, distance=5, note="15-foot beam"),),
    "gasoline-gallon": (_feature("fuel", capacity=1, note="one gallon"),),
    "gps-receiver": (
        _feature(
            "sensor",
            duration=24 * 3600,
            trait="trait:absolute-direction",
            note="requires satellite signal",
        ),
    ),
    "grapnel": (_feature("support", capacity=300, distance=2, note="throw to ST x 2 yards"),),
    "group-basics": (
        _feature(
            "tool",
            skill="skill:survival",
            capacity=8,
            note="basic Cooking and Survival gear for 3-8 campers",
        ),
    ),
    "iron-spike": (_feature("tool", skill="skill:climbing", note="piton and door spike"),),
    "kerosene-gallon": (
        _feature("fuel", capacity=1, duration=16 * 3600, note="one camp-stove gallon"),
    ),
    "lantern": (
        _feature("light", duration=24 * 3600, consumable="equipment:lantern-oil-pint", units=1),
    ),
    "life-jacket": (_feature("protection", capacity=350, trait="trait:flotation"),),
    "waterproof-matches-50": (
        _feature("tool", skill="skill:survival", capacity=50, note="starts fire"),
    ),
    "lantern-oil-pint": (
        _feature("fuel", capacity=1, duration=24 * 3600, note="one lantern pint"),
    ),
    "parachute": (
        _feature(
            "protection",
            skill="skill:parachuting",
            distance=80,
            modifier=5,
            note="opens after 80 yards; descends five yards per second",
        ),
    ),
    "personal-basics": (
        _feature("tool", skill="skill:survival", modifier=0, note="absence gives -2"),
    ),
    "rope-3-8": (_feature("support", capacity=300, note="10-yard rope"),),
    "rope-3-4": (_feature("support", capacity=1100, note="10-yard rope"),),
    "scuba-gear": (_feature("breathing", skill="skill:scuba", duration=2 * 3600),),
    "insulated-sleeping-bag": (_feature("protection", modifier=3, trait="hazard:freezing"),),
    "hard-suitcase": (_feature("container", capacity=100, modifier=4, note="DR 4 key lock"),),
    "thermos-bottle": (
        _feature("container", capacity=1, duration=72 * 3600, note="hot 24 hours; cold 72 hours"),
    ),
    "torch": (_feature("light", duration=3600),),
    "water-purification-tablets-50": (
        _feature("tool", skill="skill:survival", capacity=50, note="one quart per tablet"),
    ),
    "battery": (_feature("fuel", note="replaceable electrical power"),),
    "drum": (_feature("communication", distance=3 * 1760, note="audible for several miles"),),
    "mini-recorder": (
        _feature(
            "recording", duration=3 * 3600, consumable="equipment:mini-recorder-tape", units=1
        ),
    ),
    "digital-mini-recorder": (_feature("recording", duration=3 * 3600, note="digital recording"),),
    "transistor-radio": (_feature("communication", duration=8 * 3600, note="receive-only"),),
    "mini-tv": (_feature("communication", duration=4 * 3600, note="receive-only video"),),
    "bit-and-bridle": (
        _feature("mount", skill="skill:riding", modifier=2, note="+3 using both hands"),
    ),
    "horseshoes": (_feature("mount", skill="attribute:ht", modifier=2, note="long-ride stamina"),),
    "saddle-and-tack": (_feature("mount", skill="skill:riding", note="basic riding equipment"),),
    "spurs": (_feature("mount", skill="skill:riding", modifier=1, note="control mount"),),
    "stirrups": (_feature("mount", skill="skill:riding", modifier=1, note="required for Lance"),),
    "war-saddle": (
        _feature("mount", skill="skill:riding", modifier=1, note="stay seated; unconscious 50%"),
    ),
    "audio-bug": (
        _feature(
            "recording", modifier=-7, duration=7 * 86400, distance=440, note="audio transmitter"
        ),
    ),
    "bug-stomper": (_feature("sensor", duration=8 * 3600, distance=10, note="jams bugs"),),
    "disguise-kit": (_feature("tool", skill="skill:disguise", modifier=1),),
    "electronic-lockpicks": (
        _feature("tool", skill="skill:lockpicking", modifier=2, note="electronic locks only"),
    ),
    "handcuffs": (_feature("restraint", skill="skill:escape", modifier=-5),),
    "homing-beacon": (
        _feature("sensor", duration=12 * 3600, distance=1760, note="scanner-tracked beacon"),
    ),
    "laser-microphone": (_feature("sensor", distance=300, note="eavesdrop through glass"),),
    "lockpicks": (_feature("tool", skill="skill:lockpicking"),),
    "nanobug": (_feature("recording", modifier=-10, note="audio-visual bug"),),
    "shotgun-microphone": (
        _feature("sensor", modifier=1, trait="trait:parabolic-hearing", note="levels equal TL-5"),
    ),
    "binoculars": (
        _feature("sensor", modifier=2, trait="trait:telescopic-vision", note="levels equal TL-4"),
    ),
    "camcorder": (
        _feature(
            "recording", duration=7 * 3600, modifier=5, trait="trait:night-vision", note="10x zoom"
        ),
    ),
    "camera-35mm": (_feature("tool", skill="skill:photography", capacity=32),),
    "digital-mini-camera": (_feature("recording", note="optical-disk still pictures"),),
    "spy-camera": (_feature("recording", capacity=36, note="microfilm exposures"),),
    "telescope": (
        _feature("sensor", modifier=1, trait="trait:telescopic-vision", note="levels equal TL-3"),
    ),
    "balance-and-weights": (_feature("tool", skill="skill:merchant", note="weighing goods"),),
    "three-foot-crowbar": (
        _feature("tool", skill="skill:forced-entry", modifier=-1, note="small mace in combat"),
    ),
    "cutting-torch": (
        _feature(
            "tool",
            skill="skill:machinist",
            duration=30,
            consumable="equipment:cutting-torch-gas-bottle",
            units=1,
            note="1d+3(2) burn each second",
        ),
    ),
    "cutting-torch-gas-bottle": (_feature("fuel", duration=30),),
    "pickaxe": (_feature("tool", skill="skill:digging", modifier=4, note="ordinary-soil breakup"),),
    "iron-plow": (_feature("tool", skill="skill:farming", note="works rough soils"),),
    "wooden-plow": (_feature("tool", skill="skill:farming", note="ox-drawn"),),
    "saw": (_feature("tool", skill="skill:professional-skill-lumberjack"),),
    "shovel": (_feature("tool", skill="skill:digging", modifier=2),),
    "spinning-wheel": (
        _feature(
            "tool",
            skill="skill:professional-skill-spinner",
            modifier=6,
            note="six times normal yarn rate",
        ),
    ),
    "wheelbarrow": (_feature("support", capacity=350, modifier=5, note="load counts one-fifth"),),
    "whetstone": (_feature("tool", skill="skill:armoury", note="sharpens tools and weapons"),),
    "bandages": (_feature("medical", skill="skill:first-aid", capacity=6, relative=True),),
    "crash-kit": (
        _feature(
            "medical",
            skill="skill:first-aid",
            modifier=2,
            note="Surgery improvised at -5",
            relative=True,
        ),
    ),
    "first-aid-kit": (_feature("medical", skill="skill:first-aid", modifier=1, relative=True),),
    "surgical-instruments": (_feature("medical", skill="skill:surgery", relative=True),),
    "portable-carpentry-tool-kit": (_feature("tool", skill="skill:carpentry"),),
    "portable-armoury-tool-kit": (_feature("tool", skill="skill:armoury"),),
    "portable-explosives-tool-kit": (_feature("tool", skill="skill:explosives"),),
    "portable-machinist-tool-kit": (_feature("tool", skill="skill:machinist"),),
    "portable-mechanic-tool-kit": (_feature("tool", skill="skill:mechanic"),),
    "portable-electrician-tool-kit": (_feature("tool", skill="skill:electrician"),),
    "portable-electronics-repair-tool-kit": (_feature("tool", skill="skill:electronics-repair"),),
    "suitcase-lab": (
        _feature(
            "tool", skill="skill:science", note="authored scientific specialty", relative=True
        ),
    ),
    "ear-muffs": (_feature("accessory", trait="trait:protected-hearing"),),
    "hip-quiver": (_feature("accessory", capacity=20, note="arrows or bolts"),),
    "belt-holster": (_feature("accessory", capacity=1, note="pistol-sized item"),),
    "shoulder-holster": (
        _feature(
            "accessory",
            skill="skill:fast-draw-pistol",
            modifier=-1,
            note="permits Holdout",
            holdout=True,
        ),
    ),
    "leather-lanyard": (
        _feature(
            "accessory", modifier=2, target="weapon", note="lanyard; DX Ready retrieval; DR 2 HP 2"
        ),
    ),
    "woven-steel-lanyard": (
        _feature(
            "accessory", modifier=6, target="weapon", note="lanyard; DX Ready retrieval; DR 6 HP 4"
        ),
    ),
    "laser-sight": (
        _feature(
            "accessory",
            modifier=1,
            duration=6 * 3600,
            target="weapon",
            note="visible dot also gives target +1 Dodge",
        ),
    ),
    "scope-4x": (
        _feature("accessory", modifier=2, target="weapon", note="requires two seconds Aim"),
    ),
    "thermal-scope-4x": (
        _feature(
            "accessory",
            modifier=2,
            duration=2 * 3600,
            target="weapon",
            trait="trait:infravision",
            note="requires two seconds Aim",
        ),
    ),
    "shoulder-quiver": (_feature("accessory", capacity=12, note="arrows or bolts"),),
    "pistol-smg-silencer": (
        _feature("accessory", modifier=-4, target="weapon", note="-1 damage per die; Hearing -4"),
    ),
    "web-gear": (_feature("accessory", note="belt, suspenders, pouches and rings"),),
}

_electronic_replacements = {
    "backpack-radio",
    "cell-phone",
    "hand-radio",
    "headset-radio",
    "laptop",
    "metal-detector-wand",
    "night-vision-goggles",
    "satellite-phone",
    "secure-headset-radio",
    "wearable-computer",
}
_blocked = {
    row.definition_id.removeprefix("equipment:") for row in ORDINARY if row.unsupported_mechanics
} - _electronic_replacements
if _blocked != set(_FEATURES):
    raise RuntimeError(
        f"B288-289 feature registry drift: missing={sorted(_blocked - set(_FEATURES))}, "
        f"extra={sorted(set(_FEATURES) - _blocked)}"
    )

ORDINARY = tuple(
    row.model_copy(
        update={
            "general": _FEATURES.get(row.definition_id.removeprefix("equipment:"), ()),
            "unsupported_mechanics": (),
        }
    )
    if row.definition_id.removeprefix("equipment:") in _FEATURES
    else row
    for row in ORDINARY
)
