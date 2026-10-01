# Power-supported item spell maintenance

Reviewed against the supplied Basic Set Characters third printing B238 and
Campaigns fourth printing B480–482, for the bounded Power lifecycle in #785.

B480 reduces an item's listed spell casting and maintenance energy using the
Power enchantment. A paid cast whose upkeep reaches zero becomes continuously
supported while its wearer remains awake. The existing cast still uses the
ordinary item casting time, roll, resistance, energy, and original subject.
B238 renews maintenance at each duration boundary without a new roll or elapsed
action. The spell retains its ordinary break conditions and spells-on penalty.
Numeric zero on a spell with no maintenance entry, such as Fireball, does not
make that spell maintainable.

The private `power_lifecycle` module records an ordinary cast's exact original
actor, subject, channel, item, spell binding, Power source fingerprints, listed
upkeep, and duration. Its checkpoint extends that same spell effect across
elapsed maintenance boundaries at no additional energy cost. The canonical
item-wide resolver still controls Power, including low-mana scaling exactly
once and mage-only restrictions across the surviving item. Binding replacement,
loss of current approval, or a changed channel cannot reuse prior authority.
Private provenance leaves the public spell/effect vocabulary unchanged. A charged
cast spends only the exact admitted binding, preserving exhausted historical
bindings retained after physical loss and a later fresh enchantment.

Sleep prevents the next renewal; it does not retroactively erase the current
paid interval (B238). B482 makes the always-on support depend on wearing or
carrying the item. Removing or transferring it ends the supported effect.
Physical breakage permanently loses its enchantments under the existing B480
loss record; ordinary repair cannot reactivate them. No-mana stops support
without destroying the enchantments, and insufficient current Power likewise
cannot supply free upkeep. Expiry, cancellation, and spell-specific endings are
terminal for this cast. Restoring an item or waking later does not fill missed
intervals or recreate the effect. A later fresh authorized cast remains possible
when the item again works; the supplied source does not settle automatic
revival of a previously paid effect after a no-mana interruption.

The PlayService join calls `checkpoint(runtime, state, before=before)` after
item-loss reconciliation and before spell consumers. It runs inside the existing
transaction and seeded-command replay. Renewal IDs derive from the original
cast and its due boundary. A repeated checkpoint creates no additional event,
roll, energy spend, or effect. Genesis rejects private origin records supplied
as initial player state.

B480's zero-casting-cost branch must use a suitable, explicitly declared wearer
effect as described by B482. Existing outward Light, Daze, and Fireball cast
channels cannot implement that by silently retargeting or granting unlimited
casts; these combinations are explicitly rejected. This implementation does not
add wearer effects, missile regeneration, new spell families, duplicate Power
composition, or public commands.

Tests use real SQLite and PostgreSQL spell services to verify ordinary casting,
original external targets, multiple maintenance boundaries, spells-on counting,
current authority, sleep, item loss, cancellation, retry/restart, event replay,
and seeded command reexecution. Focused boundary cases also cover current mana,
numeric-zero missile upkeep, and recalculation of manual item maintenance from
current Power rather than a cached energy discount.
