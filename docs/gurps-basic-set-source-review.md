# Basic Set selected-source review (#191)

This record covers the user-supplied *Characters*, Fourth Edition, third
printing (February 2008), and *Campaigns*, Fourth Edition, fourth printing
(April 2008). Their checked SHA-256 identities are recorded in the certification
ledger. The unavailable August 2004 *GURPS Lite* artifact remains outside this
review and continues to block Lite certification.

The review compared the checked-in source ledger with the two supplied files in
three independent passes:

- PDF outline destinations were enumerated for every structural section and
  checked against the printed page footer. This found and corrected the former
  one-page-low offset for all *Campaigns* destinations.
- The 480 named trait rows and seven trait-list rollups were checked against the
  printed B297-B300 lists, preserving repeated names and positive/negative rows
  as distinct source identities.
- The 87 enhancement, limitation, and gadget-limitation rows were checked
  against the printed B300-B301 lists, including same-name entries in different
  modifier classes.

`scripts/import_basic_set_source_ledgers.py` reproduces the 711 section, 487
trait, and 87 modifier identities from those exact files. CI validates source
digests, page bounds, stable denominator identity, parent links, profile-scoped
runtime joins, review evidence, and open ownership for every unresolved
mechanic. The review records source identity and disposition only: it does not
promote absent or partial runtime behavior.

Every selected-source row is therefore reviewed and assigned either a concrete
mechanics owner or a non-mechanics disposition. Issue #493 records explicit
disabled decisions for all eleven named optional rules, and #494 records the
Chapter 20 Infinite Worlds exclusion in Basic Set profile version 10. Those are
reviewed dispositions, not hidden gaps or claims of executable behavior. Exact
behavior and fixture certification remain with their recorded child owners,
while the unavailable Lite printing remains a separate blocker.

#744 reconciles the optional section joins with those profile decisions. B417's
cinematic rules and independent Dual-Weapon Attacks box are disabled in the
selected generic profile, rather than required/verified through armor-divisor
evidence. Standard Basic Set magic and psi remain required; they are not a
blanket optional exclusion. Source identity review still does not certify their
unresolved behavioral effects.

## Resistance fixture correction (#754, 2026-09-30)

The supplied Campaigns fourth printing, B348 Resistance Rolls, requires the
attacker to succeed as well as win the contest; ties favor the subject. The
`basic-resistance-both-fail` fixture has an attack target of 12 and a total of
13. Its previous `affected: true` expectation incorrectly applied the ordinary
Quick Contest rule for two failures. Independent re-review changes that
expectation to `false`; the ordinary contest still records its margin-of-failure
winner. The fixture review fingerprint now covers the corrected expectation
and provenance. This correction does not promote any other source or mechanic.

## Regular Contest extreme-score correction (2026-10-24)

Re-review of the exact uploaded *Campaigns* fourth printing, B349, found that
the previous high/low Regular Contest expectations disagreed with the selected
source. The printed thresholds are inclusive: both scores at most 6, or both
at least 14. In either case the lower becomes 10 and the same offset applies
to the higher. If both exceed 20, the lower becomes 10 and the higher is
multiplied by 10/lower, rounding down. Independently entered examples include
5 versus 3 becoming 12 versus 10, 19 versus 16 becoming 13 versus 10, and
600 versus 500 becoming 12 versus 10. A 660-versus-500 case checks floor rounding.

The old fixture named `basic-regular-contest-no-levelling-at-14` encoded the
wrong exclusive threshold. It is replaced by `basic-regular-contest-levelling-at-14`,
and the other affected fixture expectations, input dice and review fingerprints
are explicitly corrected. All Basic Set Regular Contest fixtures remain required
by the execution binding; adding cases does not bypass execution or source checks.

The shared scorer now records separate adjustments for each contestant, without
changing the durable trace shape. Recorded legacy adjustment reasons preserve
historical receipt replay; mixed, changed or otherwise inconsistent receipts
refuse replay. Fresh pin commands capture the private
`regular-contest-extreme-scores` feature; historical absence retains old pin
scoring. Real pin tests exercise resulting pin state, authority, stale revision,
restart retry and seeded command replay on both database backends. Lite source
behavior is unchanged; this source review covers only the selected Basic Set.

Immediate Interrogation also records the decision in its private captured social
context: new sources opt into the corrected rule and omitted historical fields
retain old scoring. Tests reexecute both captures on SQLite and PostgreSQL.
The callback-supplied context is copied rather than changed. Automatic NPC Interrogation, including a pending director choice resolved by
Wait or an ordinary task clock, also captures the decision. Private social
semantics version 2 enables the B349 correction; omitted versions and version 1
retain their prior reaction and contest behavior. The central command pipeline
restores both decisions during replay, rather than inferring them from a current
profile. Tests preserve versions 0/1/2, actual disclosure, NPC action budgets,
authority, exact retries and seeded reexecution on both stores. Generic
mundane procedure APIs use the corrected fresh service and preserve old receipts
through their recorded adjustment reason; they have no additional persisted
command dispatcher importing their `attempt` entry point.
