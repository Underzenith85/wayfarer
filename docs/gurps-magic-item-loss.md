# Magic-item loss and Power authority

Source: Basic Set Campaigns, fourth printing, B480–482. This is a bounded part
of issue #785; it does not certify the remaining Staff/Melee-dependent item work.

B480 physical breakage or destruction permanently loses the item's magic. Damage
alone, including reaching zero HP while the object still works, does not. The
object reducer and shared play checkpoint retain, for known magic items only, an
immutable private record of
the completed binding IDs lost at breakage. They preserve object damage, combat,
repair, and completed enchantment-project evidence. Mundane breaks gain no magic
event. Existing object-result evidence also prevents static legacy bindings from
resurrecting after ordinary repair when no magic record was stored. Ordinary repair cannot
restore a recorded binding or a legacy static campaign binding. A genuinely new
enchantment receives a fresh binding ID and can work after the item is repaired;
the distinction does not depend on timestamps.

B481 requires each enchantment's own effective Power to reach 15, including the
temporary low-mana penalty; no-mana prevents use. B482's mage-only restriction
applies across all surviving enchantments on the same item. Lost historical
bindings remain available as evidence but do not restrict later new magic.

B480 Power reduces casting and maintenance energy for the other spells on the
same item. The item-wide resolver returns the single Power enchantment's raw
level only when that enchantment's own Power works in the current mana. The
existing energy-cost calculation scales the level for mana exactly once. Legacy
inline reductions retain their existing per-binding scope, including independent
reductions on multiple legacy spell bindings. Personal spell costs
are not changed by these helpers.

Duplicate Power, Power upgrades, and mixed passive/inline Power composition
remain explicitly unsupported. The supplied Basic Set does not settle their
composition: B237's strongest-variable rule excludes permanent effects, and B480
enchantments are permanent. The installation preflight rejects an existing
surviving Power source before new costly work, even if that source is temporarily
inactive in current mana. Lost Power history does not block later enchantment.

Focused source-numbered tests cover damage versus breakage, same-second binding
identity, actual new enchantment completion, per-enchantment Power, item-wide
casting and mage-only resolution, ordinary repair, and SQLite/PostgreSQL
object/combat transactions with restart, retry, and replay evidence.
