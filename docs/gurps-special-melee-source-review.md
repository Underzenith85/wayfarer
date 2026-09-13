# Basic Set special-melee source review (#510)

This review used the supplied *Campaigns*, Fourth Edition, fourth printing
(April 2008), B398-B407, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
Referenced anatomy, combat-technique, skill, and weapon-table entries were
cross-checked in the supplied *Characters*, Fourth Edition, third printing
(February 2008). Requirements below are paraphrased; no source prose is stored.

## Executable boundaries

- Human hit locations select attack penalties, armor coverage, wounding factors,
  crippling limits, knockdown consequences, missing anatomy, and random-location
  results from the target's saved injury profile.
- Chink attacks accept only impaling, piercing, or tight-beam burning damage. The
  declared location selects the entire attack penalty, and a successful attack
  doubles the attack's armor divisor. A body target must have equipped armor that
  covers the location; object attacks retain the shared durability reducer.
- Weapon targeting derives its modifier and geometry from the target item. Hits
  dispatch through the same object-damage reducer used by other object attacks.
- Nonlethal melee can commit a lower-than-actual ST, turn a swinging cutting edge
  into a crushing flat strike, or reverse a thrusting impaling weapon for reduced
  crushing damage. Existing pin, arm-lock, Choke Hold, and suffocation transitions
  remain the authoritative restraint and unconsciousness paths.
- Vertical separation and each fighter's own reach independently determine legal
  targets and defense adjustments. Positive Size Modifier extends only upper
  reach and grants a relative-size bonus to grapple attacks; SM comes from the
  approved character build.
- Attack-from-above, special unarmed techniques, improvised-weapon classification,
  dirty tricks, and special weapon defense interactions use bounded typed tables.
  A known repeated trick rejects instead of becoming a permanent combat bonus.

## Explicit exclusions

Odd leaning or hanging positions, arbitrary nonhuman anatomy, flexible armor
exceptions for joint locks, persistent embedded picks, adjustable whips, garrote
equipment state, shield rush/slam, and cinematic technique improvements require
their own authored state or catalog metadata. Callers cannot substitute free-form
bonuses for those facts. The current issue adds no profile or engine version.

Independent executable expectations are in
`tests/test_special_melee_procedures.py`; existing integration evidence remains in
`tests/test_hit_locations.py`, `tests/test_geometry_injury_followups.py`,
`tests/test_object_combat.py`, `tests/test_unarmed_parry_lock.py`, and
`tests/test_choke_hold.py`.
