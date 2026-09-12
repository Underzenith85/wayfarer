# GURPS source audit (#191)

The audit distinguishes implemented mechanics, comparisons against inspected
printings, and reconciliation with the selected source baseline. These are separate
claims. The current audit is **incomplete**; its integrity checks pass without
claiming source completeness or enabling a profile.

## Reviewed evidence

Publication metadata identifies Characters as third printing (February 2008) and
Campaigns as fourth printing. Artifact SHA-256 digests and exact metadata locators
are in `tests/fixtures/gurps/source-audit.json`. The combined volume begins with
the same third-printing Characters publication statement; it is duplicate source
evidence. Source contents remain outside the repository.

Eighteen Basic statistics fixtures were independently compared against Characters
third printing B14-17: two primary costs, two Basic Lift cases, ten damage table
rows, and four secondary-characteristic purchase/advisory cases. Their numbers
agree. Each comparison names the reviewer, pages and executable test and binds
the complete fixture with a digest. This remains `compared`, not independently
`reviewed`. Other fixture expectations remain explicitly pending.

#215 adds 36 independently compared cases for opt-in statistics revision 2:
high-ST progression, unsupported intermediate rows, lowered Will/Per permission
advisories and realistic Speed/Move purchase limits. Together with the original
18, the ledger now contains 54 compared fixtures. B15-17 provides no rule for
unlisted intermediate ST rows between 40 and 100; these explicitly remain
unsupported rather than using invented interpolation. Profile v6 / Characters
package 0.6.0 implements the supported changes without altering saved pins.
The supplied Characters third printing and Campaigns fourth printing are now the
selected baseline. These comparisons still do not claim full mechanics conformance.

The exact August 2004 Lite revision was not available for direct inspection. No
Lite source identity is asserted verified. The selected-printing baseline does
not change existing profile/package identifiers or versions.

## Inventory integration and remaining enumeration

The report reads owner inventories directly, rather than maintaining duplicate
runtime definitions:

| Inventory | Owner | Boundary |
| --- | --- | --- |
| Mundane skill families and selected expansions | #112 | All 257 rows consumed with their own item-level owners and certification state; specialties/TL and runtime blockers remain |
| Selected mundane traits and backgrounds | #113 | Selected constructions do not exhaust all entries |
| Supernatural catalog and transferred skills | #119 | All 334 typed records consumed directly; concrete runtime/source blockers remain |
| Registered catalog definitions and learned spells | #112/#113/#119 | IDs include package versions; representative coverage |
| Equipment selection and ultratech index | #180 | Item-level references retained; full catalog pending |
| Equipment table sections, footnotes, field provenance and binding | #180 | Every audited row is claimed by a section; omitted groups, unsupported behaviours, uncovered fields and unbound catalogs stay visible |
| Lite equipment gaps | #121 | Recorded separately so deferred Basic work cannot satisfy the Lite claim |
| Vehicle index | #207/#120 | Listing facts do not establish operating mechanics |

The report never derives source completeness from inventory counts. Unenumerated
rule/table/item coverage remains visible in required scope records for Lite,
Characters and Campaigns. Those records are **coverage gaps**, not a claim that
all rules have stable individual IDs. Full reconciliation with #112, #113, #119
and #180 is still necessary to finish #191. Optional rules have explicit disabled
scope decisions; exclusion from a mundane inventory is not exclusion from Basic.

## Executable checks

Run `uv run python scripts/audit_gurps_sources.py` for the combined report and
integrity checks. CI runs it and pytest exercises failure cases. Checks reject
missing/duplicate records, stale fixture fingerprints, unknown sources/profile
references, missing test bindings, missing required scopes, and registry/table
drift. All expectation-ledger cases have explicit review dispositions. The old
three-hex distance case now has an executable numeric binding as well.

The #180 equipment ledger is read directly by the same report. Its sections,
special-gear dispositions, field provenance, package binding and Lite gaps are
inventory rows with their own owning issues, so an omitted table group is a named
blocker rather than an absence. Run
`uv run python scripts/audit_gurps_equipment.py` for that ledger on its own; see
[the equipment table audit](gurps-equipment-audit.md).

`--require-complete` exits nonzero while any source, scope or fixture is unresolved.
For release evidence, `scripts/release_gates.py --gurps-source-audit` adds this
source gate to the existing test gates. Passing it would still not prove runtime
or live-play completeness; #121/#122 retain their other requirements. Ordinary
prototype release behavior is unchanged.

Editing a fixture invalidates its review. After independently rechecking the
source, update that record's digest and evidence; never regenerate expected
numbers from runtime output. `fingerprint` hashes metadata and expectations only.
A `reviewed` fixture requires a reconciled source; a later-printing `compared`
fixture remains a certification blocker.
