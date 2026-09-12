# GURPS Basic Set mana and divine traits

Issue #239 implements Magery, Magic Resistance, Mana Damper, Mana Enhancer,
Power Investiture, and Magic Susceptibility as a pinned Characters
third-printing package. Construction covers Magery 0 and its situational
limitations, Improved Magic Resistance, leveled Area Effect and Switchable
mana traits, the divine pact condition, and the source-defined exclusions.

Approved builds project spell-learning and casting bonuses, direct-spell
casting and resistance modifiers, aura visibility, divine spell eligibility,
and bounded mana-level shifts. Limited Magery requires the caller to supply
the authored college and current environmental conditions rather than silently
granting an unconditional bonus.

Switchable mana fields use authored owners and locations, actor authority,
compare-and-set revisions, optional expiry schedules, explicit deactivation,
and restart-safe event history. The adapter resolves carried effects and
same-location Area Effect fields into the existing `SpellContext`; it does not
create a second spell engine. Multiple Mana Enhancers use only the highest
increase, as specified by the source.

Inventory rows remain `partial` for source certification #191; runtime blocker
#239 is removed from all six entries.
