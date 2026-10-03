# Armoury tool quality in actual repairs

Basic Set B345 supplies basic 0, good +1, fine +2, and best
max(2, floor(TL/2)). Missing important components impose −1 each, and
minor/moderate/severe damage imposes −1/−2/−3. Those physical deficiencies
add to every basic-or-better quality modifier.

This bounded private opt-in uses a trusted current director's
`DeclareRepairTools` observation. It captures the actual owned available tool,
its pinned profile digest and object condition, quality, important component
count and categorical damage. It accepts only basic pinned carriers with no
authored quality bonus, ambiguous skill features, fuel consumption or operating
limit. The object’s `repair_tools_definition` qualifies the required toolkit;
an exact specialty feature is not invented for the generic Armoury toolkit.
Quality does not change price, grant a new item, or infer an advanced variant.

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

Tests cover actual firearm and Body Armor repair margins, additive deficiencies,
minimum-skill refusal, parts/time/default composition, restored gun firing and
armor DR, authenticated observations, exact retry/restart, current-state refusal
before dice, rollback, genesis/privacy and full seeded campaigns in SQLite and
PostgreSQL.

[#977](https://github.com/Underzenith85/wayfarer/issues/977) remains open.
Alternative equipment, no/improvised equipment, powered-tool fuel and operating
reservations, and unresolved best-equipment TL variants are unverified. This
quality slice does not claim those missing operating contracts are implemented.
