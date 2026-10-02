# GURPS Basic Set movement spells

The legacy #223 inventory groups Apportation, Armor, Deflect Missile, Great
Haste, Haste, Lockmaster, Magelock and Shield. Inventory membership is not an
executable effect. Unreviewed construction and effect families remain explicit
manual boundaries. Magelock's learned college is Protection and Warning,
although the historical package groups it with movement spells.

## Haste (#797)

Bounded private Haste casting and source-valid Power wearer activation now change
actual walking Move and Dodge, with shared energy and lifecycle behavior. See
[Haste and Power wearer effects](gurps-haste-power-wearer.md) for evidence,
compatibility and explicit remaining limits. Apportation and Great Haste remain
unimplemented; #797 stays open.

## Lockmaster and Magelock (#799)

Reviewed against the supplied Basic Set: Characters, third printing (February
2008), printed B251 and B253. Magelock is on B253, beyond the original issue's
B251–252 locator. B235–239 and B241–242 supply shared construction, timing,
energy, targeting and contests. Campaigns B383 supplies the later physical
Ready operation. Source PDFs and copied source passages are not included.

| Spell | Learning | Casting | Object consequence |
| --- | --- | --- | --- |
| Lockmaster | IQ/Hard; Magery 2 and purchased Apportation | 10 seconds; 3 energy; Regular range and Lockpicking difficulty | Unlocks the mechanical lock and contests each existing Magelock using its original effective casting skill. The unlocked door still requires physical opening. No maintenance. |
| Magelock | IQ/Hard; Magery 1 | 4 seconds; 3 energy; 2 to maintain | A closed door gains a six-hour magical closure. Active effect state, rather than a permanent lock flag, controls later opening. |

Apportation's Magery 1 construction prerequisite is present only to support the
Lockmaster learning chain. Its movement effect and the other inventory spells
are not implemented by this change. Regular energy scales by 1 + positive SM;
negative SM does not discount it. Skill-based casting benefits apply after the
source cost calculation. Range is measured at the actual casting roll;
an unseen, untouched subject adds the separate -5, while actual contact removes
both penalties. B345 forbids a casting roll below final effective skill 3,
including live condition modifiers; the caster can cancel an unfinished cast.
Mapped contact uses current actor/object separation, not a
weapon's previously selected reach. Physical barriers do not block Regular
magic. B241 excludes spell subjects from the Rule of 16; a B242 contest tie
resists. One casting roll is reused against all independent wards, and critical
success bypasses resistance. Multiple fixed wards do not add a resistance bonus.

## Ordinary ritual availability (B237)

The host checks the caster's actual ordinary ritual at casting start, each
continued Concentrate command and completion. The relevant score is trained
spell skill, adjusted only for low mana. At skill 9 or less both hands and feet
must be free and speech available; at 10–14 speech and a gesture are needed;
at 15–19 speech or a small gesture suffices; at 20 or more neither is required.
Range, shock and HP-energy penalties do not change that ritual category.
Cancellation, maintenance and remembering do not repeat the casting ritual.

Canonical limb injuries, held items, arm/leg grips, movement-form limitations
and approved Mute/Cannot Speak purchases are consumed as actual restrictions.
Occupied hands are not automatically unable to gesture at skill 10–19. Nor
does a generic restrained flag identify whether fingers or head can move.
`SpellRitualService` lets the current trusted director record private typed
speech, gesture and full-body availability for these ambiguous facts. A positive
observation cannot override the low-skill canonical hand/foot requirements or
an approved speech disadvantage. The observation is bound to the approved
build revision and must be refreshed after that build changes. No narrative
inference or caller-supplied skill, energy or ritual category is accepted.
Speech-only and mental-only casting remain possible under physical restraint;
consciousness and other independent concentration requirements still apply.

`tests/test_spell_rituals.py` verifies real approved skill boundaries, low mana,
limb loss, occupied hands, speech/gesture alternatives, mid-cast changes,
private projections, current GM authority, exact retries and seeded replay.
These ordinary checks do not promote the separate ceremonial/item/staff paths.

## Persisted object and command boundaries

`LockService` records trusted director-authored fixtures, immutable channels,
and contextual backfire alternatives. `LockSpellService` dispatches the pair
through the shared private runtime spell schema and spell reducer. Current
approved builds supply actual skill, Magery, energy pools and prerequisites.
The frozen public spell/command/event enums remain unchanged; new typed records
use private resource-event prefixes. Authored genesis cannot seed execution
receipts. No HTTP/UI schema expansion is implied.

Fixture state distinguishes physical closure, a mechanical lock, and live
Magelock effects. Ordinary world movement and the actual scene-exit service
consult the same passage state. Outside combat opening/closing consumes one
second and settles the synchronized subgroup clock. In combat an ordinary Ready
targets the object, requires an available usable hand and current nearby mapped
placement, and changes it only after any canonical grapple/close-combat DX
check succeeds. Reaching through a previously declared impossible or off-map
channel is rejected before that channel is persisted.

A fixed door may bind one canonical durable item on matching world ground.
The same item cannot back two fixtures or be retrieved intact as carried gear.
Object destruction and completed salvage retire the physical barrier even if
its remains are later consumed; persisted canonical result history survives
restart. A missing unexplained backing object fails validation. Repairs and
salvage require workpieces, tools and contained supplies at the actor's actual
world location; on-site work preserves the fixed door's ground placement.
No new door HP, DR, material or recovery yield is invented by the spell host.

Cancellation, maintenance, expiry, distraction and backfires use shared
lifecycle transitions. Maintaining/cancelling an existing effect does not
require new range or map placement merely because the caster entered an
encounter later. Retarget/reversal decisions change actual object state and
retain the original modified casting skill. Source-inappropriate person-target
rows for these object spells can be rerolled through the shared authorized
backfire resolver. The shared B236 malign appearance can create a canonical
encounter from an authored map and approved reserve actor after noncombat
casting; see [spell execution](gurps-spell-execution.md).

## Evidence and limits

`tests/test_lock_spell_construction.py` has valid and invalid learning cases.
`tests/test_lock_spell_effects.py` independently checks source numbers, duration,
maintenance/cancellation, target restrictions, real unlocking, failed/tied
contests, critical success, size, range, and canonical destruction.
`tests/test_lock_spell_persistence.py` and `test_lock_spell_combat.py` exercise
approved learning through real commands, actual later opening/movement/scene
travel, combat turns, private projections, current authority, exact retries,
CAS races, restart and seeded command re-execution on SQLite and PostgreSQL.
`test_equipment_worksite.py` covers local/remote durable work and supplies.

These are bounded executable object contracts. Arbitrary narrative locks,
mobile-fixture placement inference, unconfigured door durability, staff/magic-item
or ceremonial channels for the private pair, and autonomous summoned-creature AI
are not silently synthesized. Counterspell is a separate spell family, although
Lockmaster, cancellation, expiry and destruction end this pair's applicable
barriers. Unrelated college effects and full Basic Set certification remain
outside this evidence.
