# GURPS equipment table audit (#180)

`wayfarer.simulation.equipment_audit` is evidence accounting for the selected
equipment tables. It contains no rules prose, no invented rows and no second
mechanics engine. Four claims are kept apart on purpose:

- which rows the repository actually recorded, and from which page anchor;
- which row groups are **explicitly omitted**, and what blocks them;
- how each special gear behaviour is dispositioned;
- what unit and source anchor each equipment schema field carries.

Row counts never imply source completeness. The audit is **incomplete**, it says
so in its own report, and nothing here enables a profile or a release claim.

Run:

```bash
uv run --frozen python scripts/audit_gurps_equipment.py
uv run --frozen python scripts/audit_gurps_equipment.py --require-complete
```

The second form exits nonzero while any blocker remains, which is the current
state. CI runs the first form; `tests/test_equipment_audit.py` exercises the
integrity checks and their failure cases.

## Selected-table inventory and omitted rows

`ledger.json` splits B264-289 into fourteen sections. Every registered row
(`BASIC_EQUIPMENT` plus the blocked `ULTRATECH_INDEX`) belongs to exactly one
section, and the validator rejects a ledger whose sections do not cover the
pinned catalog exactly. **No section is complete.** Seven carry an inspected page
anchor and record what they leave out:

| Section | Anchor | Rows recorded | Omitted |
| --- | --- | --- | --- |
| `melee-weapons-b271` | B271-274 | 45 | non-equipment attacks and rows the typed schema cannot represent without inventing values |
| `muscle-powered-ranged` | B275-276 | 18 | duplicated thrown modes, special binding damage, launcher/cocking-aid behavior and remaining material/ammunition variants |
| `firearms` | B278-279 | 7 | remaining pistols and SMGs, gyroc acceleration, smartguns, automatic-only and high-cyclic RoF, rifles and shotguns |
| `ammunition` | B275-278 | 12 | alternative missiles, ammunition variants, explosive warheads and power cells |
| `beam-weapons-b280` | B280 | 3 | every other beam row; the three recorded rows are index facts that cannot be equipped or fired |
| `body-armor-b283` | B283 | 8 | split-DR, single-facing, flexible, layered and footnoted rows, plus the other armor pages |
| `general-equipment-b288` | B288 | 10 | every other B288 row and the whole B289 continuation |

The remaining seven sections record **no rows at all**: wealth and legality,
shields, heavy weapons, split-DR armor, higher-TL variants, weapon accessories
and the general equipment remainder. Their anchors are recorded as `range-only`,
meaning B264-289 as a range that nobody has reconciled item by item. A
`range-only` anchor is a coverage gap, not a page citation.

The B278 adapter now preserves chambered `+1` capacity separately and keeps
per-round ammunition mass as an exact rational number of millipounds. This
allows the TL6 9mm automatic pistol's 0.4-pound, nine-round load to remain
exactly `400/9` millipounds per round without inventing a rounded unit value.

One structural consequence remains recorded rather than smoothed over: no
audited row is a shield. B275-278 now provide direct ranged cases for rated ST,
accuracy, ST-multiplied and absolute range, reload timing, shots, rate of fire,
recoil, bulk, firearm action and missile references.

## Footnotes and special gear behaviour

Every special behaviour is dispositioned as `implemented` or `unsupported`.

An `implemented` behaviour names a declared conformance capability and an
executable test binding. The inspected B272-274 rows now provide direct cases for
nonzero parry modifiers and two-handed grips. Special melee behavior that the
engine cannot yet execute remains explicit and blocks only the affected item:
alternate thrown modes, stuck picks, flail defense penalties, mounted lance
damage, variable reach, conditional post-attack readiness, and the cutlass hilt.
Rows whose values do not fit the schema are explicit omissions: superscience TL,
extra dice added to ST-based damage, and variable minimum ST.

An `unsupported` behaviour names an owning issue and nothing else. Its identifier
is the same string an entry lists in `unsupported_mechanics`, and the validator
rejects any catalog blocker without a matching unsupported disposition. Those
entries already fail `inventory_spec()` and `EquipmentCatalog.bind()`;
`require_supported()` and `validate_selection()` reject them for scenario and
character selection with the owning issue in the message.

Power cells are recorded as an unsupported behaviour of their own
(`power-cell-charges`) precisely so that they are not modelled with the existing
per-round ammunition path: a rechargeable cell holds charge, not disposable
rounds. Smartguns, linked afflictions, surge damage and beam environmental
effects are recorded the same way. `weapon-breakage` is implemented as of #173,
but no audited row declares a weapon quality, so the column has no
selected-table case behind its executable one.

## Field provenance carried forward from #101

Every field of `Provenance`, `Damage`, `Parry`, `MeleeMode`, `RangedMode`,
`Armor`, `Shield` and `EquipmentProfile` has one record giving its unit, its
source anchor and either its executable coverage or an explicit gap. Adding or
removing a schema field without updating the ledger fails the audit.

All 111 records are `pending`. Nothing has been reconciled against an inspected
printing, so no field, unit or numeric sample is source-verified, and
`gurps.equipment.weapon_profiles` and `gurps.equipment.armor_profiles` stay
partial. Four fields have no direct case at all: `MeleeMode.kind`,
`RangedMode.kind`, `Shield.skill_id` and `EquipmentProfile.shield`.

Weights are thousandths of a pound throughout, including container capacity;
prices are dollars and retain fractional values for the B276 ten-cent missiles.
These are the adapter's explicit units, recorded per field so that a reviewer
checks them against the source rather than inferring them.

## Package binding

Neither audited catalog binds to the pinned packages. No registered package
declares an equipment definition, and the Basic Set rows cite the source ID
`sjg:gurps-basic-set-4e-2004`, which no package declares either. Both catalogs
are therefore recorded as `unbound`, and the recorded status is checked against
the registry on every run, so registering those definitions forces the ledger to
be updated. Until then, "mechanically complete supported entries bound through
pinned packages" is not satisfied and every audited row resolves only against
test doubles.

## Lite gaps

Lite-required gaps are recorded separately from the Basic Set tables so that
#121 cannot pass merely because Basic catalog work is deferred. The Lite sample
is two rows, its general equipment is absent, and the exact August 2004 revision
was never inspected. `scripts/lite_certification.py` reports each gap as its own
error, and `supported_equipment("gurps-lite-4e-2004")` refuses to produce a Lite
allowlist while any gap remains.

## Reporting

`source_audit.inventory()` reads these rows directly instead of keeping a second
copy, so omitted sections, unsupported behaviours, uncovered fields, unbound
catalogs and Lite gaps all appear in the combined audit and in the Basic Set
certification report with their owning issues. Five audit scopes
(`equipment-sections`, `equipment-footnotes`, `equipment-field-provenance`,
`equipment-package-binding`, `lite-equipment-gaps`) join the existing
`equipment-catalog` scope and are all unreviewed.

Editing the ledger invalidates its review. After independently rechecking the
source, update the affected record's status and evidence; never regenerate an
expected number from runtime output.
