# GURPS source audit (#191)

The audit distinguishes implemented mechanics, comparisons against inspected
printings, and reconciliation with the selected source baseline. These are separate
claims. The selected Basic Set audit is complete; the overall report remains
**incomplete** because the exact August 2004 Lite artifact is unavailable and
runtime mechanics retain their own blockers.

## Reviewed evidence

Publication metadata identifies Characters as third printing (February 2008) and
Campaigns as fourth printing. Artifact SHA-256 digests and exact metadata locators
are in `tests/fixtures/gurps/source-audit.json`. The combined volume begins with
the same third-printing Characters publication statement; it is duplicate source
evidence. Source contents remain outside the repository.

All 156 Basic Set fixture records were compared against the selected Characters
or Campaigns printing and retain a digest of the complete independently authored
expectation plus an executable test binding. The 98 Lite fixtures remain pending
because the exact selected Lite revision is unavailable.

#215 added 36 source cases for opt-in statistics revision 2:
high-ST progression, unsupported intermediate rows, lowered Will/Per permission
advisories and realistic Speed/Move purchase limits. B15-17 provides no rule for
unlisted intermediate ST rows between 40 and 100; these explicitly remain
unsupported rather than using invented interpolation. Profile v6 / Characters
package 0.6.0 implements the supported changes without altering saved pins.
The supplied Characters third printing and Campaigns fourth printing are now the
selected baseline. Reviewed expectations still do not claim full mechanics conformance.

The exact August 2004 Lite revision was not available for direct inspection. No
Lite source identity is asserted verified; that blocker is owned by #121. The
selected-printing baseline does not change existing profile/package identifiers or
versions.

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

The report never derives source completeness from inventory counts. The
711-section, 487-trait, and 87-modifier ledgers give every selected Basic Set row
a stable identity, printed-page reference, profile membership, review state, and
mechanics or disposition owner. The former Campaigns outline offset was corrected
against printed footers. See
[the selected-source review](gurps-basic-set-source-review.md). Optional-rule
selection and the Infinite Worlds boundary remain explicit child decisions in
#493 and #494; exclusion from a mundane inventory is not exclusion from Basic.

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
