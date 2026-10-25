# Armoury tool quality in actual repairs

Basic Set B345 supplies improvised −5 for technological Armoury work, basic 0,
good +1, fine +2, and best max(2, floor(TL/2)). For basic-or-better equipment,
missing important components impose −1 each, and minor/moderate/severe damage
imposes −1/−2/−3. Those physical deficiencies add only to basic-or-better quality
modifiers. Absent or improvised equipment does not receive them again. The observation producer and
stored-observation validation use the existing `EquipmentQuality` table, including
its technological-task distinction; the ordinary nontechnical improvised −2 is
not substituted for Armoury's −5.

This bounded private opt-in uses a trusted current director's
`DeclareRepairTools` observation. It captures the actual owned available tool,
its pinned profile digest and object condition, quality, important component
count and categorical damage. It accepts only basic pinned carriers with no
authored quality bonus, ambiguous skill features, fuel consumption or operating
limit. The object’s `repair_tools_definition` qualifies the default toolkit;
an exact specialty feature is not invented for the generic Armoury toolkit.
An explicitly selected alternative must carry an existing pinned `tool` feature
naming the object's exact `repair_skill_id`. A generic Armoury selector, a related
specialty, a descriptive note, or an authenticated quality observation alone
cannot qualify a different item. The same physical-observation, profile, custody,
quality and operating-limit checks apply at selection, start and finish.
The actual selected tool is reserved by the existing repair task. Unselected
repairs retain the exact default-kit lookup, even when an alternative is present.
Quality does not change price, grant a new item, or infer an advanced variant.

A current director may explicitly observe a suitable physical kit as improvised.
It may be the default kit or a selected alternative meeting the exact pinned
repair-skill qualification above. The ordinary observation and selection must
precede repair: a player cannot supply a −2 improvisation modifier or turn an
unrelated object into a toolkit. Accepted work captures the resulting −5 in its actual skill, parts assessment
and check. Observed missing components and damage remain authenticated physical
facts, but do not add penalties to the improvised tier. Insufficient
effective skill refuses before parts or repair dice. Absent equipment is a
separate unsupported path; this lane always requires the observed owned item.

Best equipment additionally requires explicit physical tool TL equal to a
concrete numeric pinned tool TL and verified personal TL tied to the current
approved build. Actual start also requires that TL to match the item's and
resolved repair training TL. VarTL and unresolved mismatches remain unsupported.
Personal TL alone cannot upgrade a low-TL kit. Missing authenticated facts refuse.

The performer records `SelectRepairTools` for one tool, damaged object and future
repair-start ID. This physical intent rolls no dice and spends no parts. It may
precede default selection: its derived bonus then participates in the existing
minimum-skill check, selected work time and durable parts assessment. Actual
start validates the current tool observation, profile, condition and custody,
then replaces the selected path's basic authored modifier with the derived
quality/deficiency modifier exactly once. Unselected historical tasks retain
their previous tool lookup, modifier, random stream and serialization.

Accepted repair skill remains captured. Finish requires the same physical
observation and tool/item condition, profiles, custody and availability; a newly
observed damaged kit requires cancellation rather than silently changing the
accepted target. Success/failure, deadline, HP restoration and parts conservation
remain the existing B484–485 transaction. Private tool facts and selections are
excluded from authored genesis and public player projections. The existing
Armoury replay family reexecutes both commands with authenticated principals.

Tests cover actual firearm and Body Armor repair margins, basic-or-better
additive deficiencies,
minimum-skill refusal, parts/time/default composition, restored gun firing and
armor DR, authenticated observations, exact retry/restart, current-state refusal
before dice, rollback, genesis/privacy and full seeded campaigns in SQLite and
PostgreSQL.

[#977](https://github.com/Underzenith85/wayfarer/issues/977) remains open.
`tests/test_armoury_alternative_tools.py` now covers the explicit skill-specific
alternative-kit slice with actual armor/firearm HP restoration, parts costs,
start/finish refusal after changed observations, reservation, restart and seeded
reexecution. `tests/test_armoury_improvised_tools.py` covers the technological
−5 improvisation modifier without adding basic-or-better deficiency penalties,
actual repair margins/HP and parts, authority/stale/refusal, changed observations,
exact retry/restart and
original-genesis seeded replay. Existing basic-or-better observation values and
serialized fields retain their prior meaning. Broader alternative qualification,
absent equipment, powered-tool fuel and operating reservations, and unresolved
best-equipment TL variants remain unverified. This quality slice does not claim
those missing operating contracts are implemented.

## Selected-source correction after PDF upload

The uploaded Campaigns fourth printing matches SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
Direct review of B345's Equipment Modifiers box confirms that additional
missing-item and damage penalties apply to basic-or-better equipment only. The
initial uncommitted improvised implementation incorrectly added those penalties.
The shared `EquipmentQuality` table and actual repair expectations now preserve
the printed distinction: a technological improvised kit remains −5, including
when its authenticated observation records missing parts or damage. The same
Armoury 15 major repair has target 9 and restores 2 HP on a total of 7 in all
three observed-deficiency cases. Basic-or-better values, stored fields and their
validation retain their existing meaning. No certification status is promoted.
