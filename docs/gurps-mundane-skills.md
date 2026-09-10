# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` is the item-level accounting for the Basic Set skill
chapter (B168–B233), and `rules/mundane_skills/technology` holds the executable
procedures #346 landed for its technology, science and vehicle rows.
**Accounting a row and implementing its procedure still do not make a skill
playable.** Every row keeps an explicit blocker, no row is available at runtime,
and no campaign profile, package pin or saved character changes because of it.

## Source boundary

| Observed source | References | Reconciliation |
| --- | --- | --- |
| Characters, Fourth Edition, third printing | B168–B233 skill chapter and index | The frozen baseline is `gurps-4e-2004-first-printing+errata-2007-01-26`. Every row carries the `first-printing-delta-audit` blocker until #191 reconciles the printings. |

Records hold identifiers, page references, controlling attribute, difficulty,
numeric defaults, prerequisites and specialty metadata. No rulebook prose is
bundled. Values not confirmed against the source stay absent and blocked rather
than reconstructed into a runnable roll.

## Accounting matrix

`python -m scripts.audit_mundane_skills` emits this matrix from the inventory
itself; the counts below are the current report, not a separate transcription.

| Accounting group | Rows | Coverage decision |
| --- | ---: | --- |
| Implemented procedures | 83 | A specific procedure resolves the row and dispatches into an existing authoritative service. The definition is still normalized to `unsupported`. |
| Structured candidate definitions | 206 | Attribute, difficulty and recorded defaults exist, but no procedure resolves the row yet. |
| Listing-only rows | 11 | Family, variable-scope and unexpanded entries whose mechanics are not recorded at all. They carry `metadata-audit` and cannot be mistaken for a definition. |
| Transferred exclusions | 28 | Cinematic and supernatural skills owned by #119 with named follow-ups #242/#243 and #191. Exclusion from this inventory is not exclusion from the Basic Set. |
| **Total accounted** | **328** | **No available row.** |

Each row is also classified by the structure it actually records, so fixtures
sample every class instead of the common shape only. Every class below must stay
populated; an unsampled class fails `validate_inventory`.

| Structural class | Rows | Meaning |
| --- | ---: | --- |
| `attribute-default` | 224 | At least one numeric attribute default. |
| `skill-default` | 18 | At least one default from another accounted-for skill. |
| `no-default` | 63 | No default is recorded; a missing default is not an implied attribute default. |
| `technology-level` | 123 | Records a TL-tagged entry. An implemented procedure supplies the B168 difference penalty; the rest still have no TL context. |
| `unexpanded-specialty` | 57 | The row itself records no specialty. A family whose children exist elsewhere in the inventory still appears here, because the class reads one row's own structure; `validate_procedures` is what refuses an implemented family with no children. |
| `listing-only` | 11 | No recorded mechanics. |
| `required-specialty` | 54 | Expanded distinct specialty with no cross-specialty inference. |
| `prerequisite` | 3 | Needs another trained skill. |
| `technique` | 6 | Parent-relative technique, not an independent skill. |
| `optional-specialty` | 1 | Optional specialty bound to its unspecialized parent. |

## Ownership and remaining blockers

Item blockers name the issue that must resolve them, and those numbers reach the
certification report directly: `source_audit` consumes each row with its own
blockers and `unsupported`/`listing-only` state instead of one family status.

| Blocker | Rows | Owner |
| --- | ---: | --- |
| `first-printing-delta-audit` | 300 | #191 printing/errata reconciliation; contextual definitions #336 |
| `runtime-procedure` | 188 | #103, #109, #110, #111, #338, #356 where named; otherwise unassigned |
| `conditional-or-skill-defaults` | 115 | #353 for the #346 group; otherwise unassigned |
| `specialty-expansion` | 50 | #356 for the #346 group; otherwise unassigned |
| `capability:gurps.vehicles.movement` | 45 | #358 |
| `technology-level-context` | 44 | #356 for the #346 group; otherwise unassigned |
| `weapon-default-audit` / `combat-procedure` | 18 | #103 |
| `metadata-audit` | 11 | Unassigned |
| `family-specialty-expansion` | 7 | Unassigned |
| `prerequisite-procedure` | 7 | #353 for the #346 group; otherwise unassigned |
| `variable-family-metadata` | 4 | Unassigned |

170 of 300 rows currently name no mechanics owner beyond this audit. The report
publishes that as `runtime_owner_unassigned`, so the gap is visible to #122
rather than implied by a family-level "partial". Naming those owners requires
dependency-linked follow-up issues and remains outstanding.

## Implemented procedures (#346)

`rules/mundane_skills/technology` implements the technology, science and vehicle
group. A procedure is declared once per family and resolved for a specialty
through its family and for a technique through its parent, so a family can never
drift from its children. Each one supplies a trusted target, typed modifiers, a
repeated-attempt policy and an outcome quantity to a service that already exists;
it never resolves the effect itself.

| Dispatch | Rows | What the service owns |
| --- | ---: | --- |
| `simulation.transport:transport-control` | 45 | Loss of control, skid, collision and occupant injury for Boating, Driving, Piloting, Shiphandling, Submarine and Crewman. |
| `simulation.noncombat:approach` | 23 | Progress and revealed facts for the information tasks (Research, Criminology, Forensics, Mathematics, Physics and the rest). |
| `simulation.hazards:resolve` | 14 | Scheduled exposure for Environment Suit, Explosives and Traps. |
| `simulation.object_repairs:record` | 1 | Recorded repair work for Electrician. |

Two modifiers are applied by the procedure itself: the B168 technology-level
difference (one point of effective skill per level, either direction) and the
B169 familiarity penalty. Handling is accepted only by a procedure that actually
steers; passing it to any other is rejected rather than ignored. A caller's
situational ruling stays a separate typed modifier in the receipt.

Eight cross-reference rows became the concrete specialties they point at:
Airshipman, Seamanship, Spacer and Submariner are Crewman (B185); Battlesuit,
Diving Suit, NBC Suit and Vacc Suit are Environment Suit (B192). Boating,
Driving, Piloting, Shiphandling, Submarine and Explosives gained their concrete
specialty rows, and the four B230–B233 non-combat techniques of this group
(Motion Picture Camera, No-Landing Extraction, Set Trap and Work by Touch) are
recorded with a parent-specific default and cap.

Thirteen scoped rows are not implemented here and are transferred, each with a
concrete open blocker rather than silence:

| Rows | Blocker owner |
| --- | --- |
| Bioengineering, Biology, Current Affairs, Disguise, Electronics Operation, Electronics Repair, Engineer, Geography, Geology, Hazardous Materials, Mechanic, Paleontology | #356 — their specialty axis is a discipline, not a vehicle class |
| Motion Picture Camera | #338 — its parent Photography belongs to the arts and trades group |

Conditional and alternative defaults and prerequisites stay blocked on #353 for
every row that records one, and every vehicle-control procedure additionally
records `capability:gurps.vehicles.movement` until #358 verifies that capability
row. `unsupported_scope()` publishes all of this to the scenario, character and
LLM validators, so an unfinished row is refused by name and by owner.

Exclusions are validated against the catalog that took them: each excluded skill
must exist in the #119 inventory with the same page and the same follow-up
issues. Drift there fails this audit instead of dropping the skill.

## Runtime contract

`require_available` rejects every unknown identifier and every blocked row, and
cleared blockers still cannot activate an unsupported definition. The candidate
package `package:gurps-mundane-skill-candidates` is separate and immutable; its
definitions carry no hooks, so scenario, character and LLM validators cannot
turn an accounted-for row into a mechanic. An implemented procedure changes
none of that: `require_available` still refuses `skill:driving-automobile` and
`skill:vacc-suit`. `tests/test_mundane_skill_technology.py` pins the procedure
expectations against `tests/fixtures/gurps/mundane_skill_technology.json`, whose
targets, margins, outcomes and unit counts were worked out from the source rules
rather than generated from the services under test.
`tests/test_mundane_skills.py` fixes
the structural classes, numeric default alternatives, specialty and prerequisite
identities, owner propagation and the exclusion transfer independently of the
audit report that consumes them.
