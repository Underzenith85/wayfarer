# General equipment and accessory execution (#686)

This implementation reconciles the 94 behavior-blocked physical rows on
Characters B288-B289: 36 B288 general-equipment rows, 46 remaining B289 rows,
and all 12 weapon/accessory rows. It also supplies executable evidence for the
Campaigns B345 equipment-modifier, B411-B412 firearm-accessory, and B425
ultra-tech-drug sections.

## Item behavior

Each affected catalog entry carries an explicit typed use, fuel supply, or
accessory adapter. Behavior is never inferred from the item's name, price, or
TL. The adapters preserve printed task bonuses, capacities, ranges, operating
times, protection, skill families, prerequisites, and consumables. The four
`var.` medical/laboratory rows retain their skill-relative TL marker; use must
supply that skill TL and the campaign TL must permit it.

The equipment reducer requires engine authority and an expected resource
revision. It verifies custody, campaign TL, LC, the selected use, and exact
consumable identity before changing state. Fuel, batteries, film, and other
supplies are conserved in authoritative item quantities or charges. Successful
uses and attachment changes write a digest-bound receipt and event. Retrying an
identical command returns its recorded outcome; reusing an ID for a different
payload fails.

## Equipment modifiers and economics

The B345 function distinguishes technological from other skills for no or
improvised equipment, implements basic/good/fine/best quality, and applies
missing-item and damage penalties only to basic-or-better equipment. The B264
starting-wealth table is available for TL0-TL12 and applies the authored wealth
multiplier without floating-point arithmetic. Every equipment row exports cost,
TL, and LC to its runtime inventory specification; selection/use rejects a
campaign context that does not permit them.

## Firearm accessories

Accessories have explicit compatibility and mounted custody. Laser sights give
the firer +1 and a target that sees the dot +1 Dodge. Fixed 4x scopes require two
seconds of Aim for +2 Acc. The thermal scope also grants Infravision. The pistol
or SMG silencer applies -1 damage per die and -4 to remote Hearing rolls while
retaining the B412 automatic-hearing and localization boundary for callers.
Quivers, holsters, lanyards, ear protection, and web gear retain their printed
capacities or modifiers and cannot be mounted on an incompatible target.

## Armor and ultra-tech drugs

Armor profiles can represent damage-type split DR, front-only protection, and
concealability. The layering helper adds DR, permits overlap only when each
inner layer is flexible and concealable, and returns the B286 -1 DX penalty per
extra non-head layer.

The B425 drug constructor requires TL9+, records trait point values, potency,
form, duration, healing family, and LC, and calculates exact duration and price.
It preserves the distinct form multipliers and the doubling of cost for each -1
of potency. The independent truth-drug fixture reproduces the printed $320,
HT-3, short-term example.

## Evidence

- Source comparison: supplied Characters third printing (February 2008),
  B264-B289; supplied Campaigns fourth printing (April 2008), B345, B411-B412,
  and B425.
- Executable fixtures: `tests/test_general_equipment.py`.
- Catalog/audit joins: `tests/test_equipment_audit.py` and
  `tests/test_basic_set_source_ledgers.py`.
