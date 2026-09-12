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
pinned catalog exactly. **No section is complete.** Ten carry an inspected page
anchor and record what they leave out:

| Section | Anchor | Rows recorded | Omitted |
| --- | --- | --- | --- |
| `melee-weapons-b271` | B271-274 | 45 | non-equipment attacks and rows the typed schema cannot represent without inventing values |
| `muscle-powered-ranged` | B275-276 | 18 | duplicated thrown modes, special binding damage, launcher/cocking-aid behavior and remaining material/ammunition variants |
| `firearms` | B278-279 | 35 | remaining firearm rows, Gauss armor division, musket rests, bipods and integral launchers |
| `ammunition` | B275-279 | 40 | remaining firearm rounds, alternative missiles, ammunition variants and explosive warheads |
| `beam-weapons-b280` | B280 | 4 | Laser Pistol and its cell are executable; Electrolaser and Blaster remain blocked with every other beam row omitted |
| `body-armor-b283` | B283 | 8 | split-DR, single-facing, flexible, layered and footnoted rows, plus the other armor pages |
| `shields` | B287 | 4 | duplicate cloak appearances, the unrepresentable force shield, and buckler, material and offensive variants |
| `general-equipment-b288` | B288 | 63 | no fixed-TL physical row; behavior-bearing items remain fail-closed |
| `weapon-accessories` | B289 | 12 | no physical row; accessory behavior remains fail-closed |
| `general-equipment-remainder` | B289 | 53 | no physical row; special effects remain fail-closed |

The remaining four sections record **no rows at all**: wealth and legality,
heavy weapons, split-DR armor and higher-TL variants. Their anchors are recorded as `range-only`,
meaning B264-289 as a range that nobody has reconciled item by item. A
`range-only` anchor is a coverage gap, not a page citation.

B288-289 now account for all fixed-TL physical rows and embedded purchasable
variants. Piton is the table's alias for Iron Spike, and Transportation is a
cross-reference rather than a physical row. The five `Var.` medical/laboratory
rows carry a skill-relative TL marker, while the portable tool-kit variants retain
their individually printed TLs. Equipment
with an operating duration, task bonus, communications effect, protection,
medical effect, or attachment rule is retained as an exact inventory record but
rejected by the selection gate on its declared unsupported mechanic.

The B278 adapter now preserves chambered `+1` capacity separately and keeps
per-round ammunition mass as an exact rational number of millipounds. This
allows the TL6 9mm automatic pistol's 0.4-pound, nine-round load to remain
exactly `400/9` millipounds per round without inventing a rounded unit value.
Four ordinary single-shot B279 long guns also preserve every numeric column and
their exact one-shot load units. They remain selection-blocked until the
dagger-marked, ST-conditioned one-hand exception can be represented directly.
Seven ordinary repeating rifles preserve chambered `+1` separately from
magazine capacity and reconstruct loaded table weight from exact rational
per-round mass. They share the same explicit one-hand blocker.
The four B279 shotgun rows likewise preserve shells separately from their nine
projectiles and retain exact per-shell load mass, including `850/7`
millipounds for the automatic shotgun.
Seven remaining ordinary handguns and the one one-handed machine pistol do the
same without a mechanics blocker. The 15mm Gyroc Pistol now carries its exact
row, individual rockets, typed close-range acceleration and smartgun behavior.
The B278 TL6 9mm SMG records its `8!` automatic-only RoF as an eight-shot
maximum and a two-shot minimum burst. Its exact 32-round load reconstructs the
listed 1.5-pound loaded-ammunition weight.
Three conventional two-handed SMG/PDW rows now preserve the same columns and
exact load reconstruction; like the long guns, they remain blocked only on the
dagger-marked one-hand exception.

The four ordinary B287 shields now provide direct cases for Shield and
EquipmentProfile.shield, including exact DB, cost, weight, DR and HP columns.
B275-279 provide direct ranged cases for rated ST, accuracy, ST-multiplied and
absolute range, reload timing, shots, rate of fire, recoil, bulk, firearm action
and missile references.

## Footnotes and special gear behaviour

Every special behaviour is dispositioned as `implemented` or `unsupported`.

An `implemented` behaviour names a declared conformance capability and an
executable test binding. The inspected B272-274 rows now provide direct cases for
nonzero parry modifiers and two-handed grips. Special melee behavior that the
engine cannot yet execute remains explicit and blocks only the affected item:
alternate thrown modes, stuck picks, flail defense penalties, mounted lance
damage, variable reach, conditional post-attack readiness, and the cutlass hilt.
Superscience and skill-relative TL markers are typed and remain unusable without
a concrete campaign TL. The Force Sword keeps its fixed damage mode, while the
Monowire Whip remains inventory-only because extra dice added to ST-based damage
are still unrepresentable. Variable minimum ST remains an explicit omission.

An `unsupported` behaviour names an owning issue and nothing else. Its identifier
is the same string an entry lists in `unsupported_mechanics`, and the validator
rejects any catalog blocker without a matching unsupported disposition. Those
entries already fail `inventory_spec()` and `EquipmentCatalog.bind()`;
`require_supported()` and `validate_selection()` reject them for scenario and
character selection with the owning issue in the message.

Power cells are individual physical inventory items with authoritative remaining
charge. They can be unloaded, source-switched and recharged only by an
engine-authorized command that names the power source; retries return the saved
receipt and cannot add charge twice. Smartguns require an explicit authorized
owner, grant their service bonus, and expose the built-in laser only when the
scene says its dot is visible. The selected Laser Pistol also requires authored
obscurant DR. Linked afflictions and surge damage remain unsupported, so the
Electrolaser and Blaster index rows still fail closed. `weapon-breakage` is implemented as of #173,
but no audited row declares a weapon quality, so the column has no
selected-table case behind its executable one.

## Field provenance carried forward from #101

Every field of `Provenance`, `Damage`, `Parry`, `MeleeMode`, `RangedMode`,
`RocketAcceleration`, `SmartgunSpec`,
`Armor`, `Shield` and `EquipmentProfile` has one record giving its unit, its
source anchor and either its executable coverage or an explicit gap. Adding or
removing a schema field without updating the ledger fails the audit.

All field records remain `pending` until the exhaustive #191 reconciliation, so
`gurps.equipment.weapon_profiles` and `gurps.equipment.armor_profiles` stay
partial. The shield skill and profile fields now have direct B287 cases;
`MeleeMode.kind` and `RangedMode.kind` still have none.

Weights are thousandths of a pound throughout, including container capacity;
prices are dollars and retain fractional values for the B276 ten-cent missiles.
These are the adapter's explicit units, recorded per field so that a reviewer
checks them against the source rather than inferring them.

## Package binding

The Basic Set catalog now binds every mechanically supported row through an
implemented definition in the pinned Characters package. Its provenance uses
the exact source ID declared by that package, and the Basic campaign policy
allowlist is the same supported set. Audit-only rows with declared unsupported
mechanics are skipped by binding and still fail `inventory_spec()` and selection;
they cannot become usable merely because their numeric inventory facts exist.

The Lite sample remains unbound because its two sample definitions are not in
the Lite package. Binding status is checked against the registry on every run,
so catalog, package, policy, and evidence drift fail tests.

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
