# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` accounts for the Characters skill chapter without making
unimplemented procedures playable, and `rules/mundane_skills/technology` holds the
executable procedures #346 landed for its technology, science and vehicle rows.
Implementing a row's procedure does not make it playable either. The candidate
package is `0.3.0`; no saved campaign pin or live representative definition
changes.

## Source boundary and completeness

The supplied **Characters, Fourth Edition, third printing (2008), ISBN
978-1-55634-729-0** is the observed source. `source_index.json` records its SHA-256
and independently indexes B301–B304, with explicit chapter expansions at
B168–B233. This observation is not verification of the selected first-printing
plus 2007-01-26 errata baseline. That reconciliation remains #336, using #191's
audit machinery. No rulebook prose is bundled.

| Source accounting | Entries |
| --- | ---: |
| Indexed skill listings, B301–B304 | 275 |
| Named technique listings, B304 | 27 |
| Explicit chapter examples and parent-specific expansions | 50 |
| **Source index total** | **352** |

The combined Combat Art or Sport listing maps to two candidate records. Thus
352 source entries map to **353 records: 325 mundane and 28 transferred** to
#119's inventory. 39 of the expansions are the concrete Boating, Driving,
Piloting, Shiphandling, Submarine and Explosives specialties #346 records.
Specialty families remain explicitly blocked where context or expansion is
incomplete. These counts do not claim enumeration of every possible
player-defined specialty.

Index reconciliation rejects missing records, unindexed additions, overlapping
transfers, invalid expansion parents and page drift. It runs when consumers load
the inventory, including the source-certification report. The exclusion transfer
also verifies names, pages and owners against the supernatural catalog.

## Accounting matrix

`python -m scripts.audit_mundane_skills` generates the report from the typed data.

| Accounting group | Rows | Decision |
| --- | ---: | --- |
| Implemented procedures | 83 | A specific procedure resolves the row and dispatches into an existing authoritative service. The definition stays unsupported. |
| Structured candidate definitions | 214 | Unsupported; source/runtime blockers remain. |
| Listing-only records | 28 | 23 technique templates and five variable families. |
| Transferred cinematic/supernatural skills | 28 | Owned by #242/#243 and source audit #191. |
| **Total accounted records** | **353** | **Zero available mundane candidates.** |

This revision fills the previously empty Aerobatics, Aquabatics, crewman, suit
and weapon entries; records Weather Sense as a TL-dependent Meteorology alias;
and accounts for Brain Hacking, Melee Weapon and every named technique. B208–B209
weapon defaults include category cross-defaults. The scope of Force Sword's
"any sword" default remains explicitly blocked. Brain Hacking's cross-package
prerequisite, variable families and context-sensitive definitions remain blocked.

The candidate package now owns its numeric data instead of inheriting it from
live representative definitions. B230 Arm Lock (Judo) and B231 Kicking (Karate)
are typed parent-relative techniques. A named template such as ST-based Neck Snap
is not converted into an ordinary DX skill.

| Structural class | Rows |
| --- | ---: |
| `attribute-default` | 227 |
| `skill-default` | 44 |
| `no-default` | 46 |
| `technology-level` | 126 |
| `unexpanded-specialty` | 59 |
| `listing-only` | 28 |
| `required-specialty` | 54 |
| `prerequisite` | 3 |
| `technique` | 6 |
| `optional-specialty` | 1 |
| `alias` | 1 |
| `technique-template` | 23 |

Classes overlap. `no-default` means no default is recorded, not a claim that
conditional defaults have been exhaustively verified. Fixtures sample every
class with independently stated source expectations.

## Remaining ownership

Every mundane row retains #112 for accounting, #336 for source/context work,
and a named procedure owner. The report maps each blocker to its owning issue;
`runtime_owner_unassigned` is zero. Historical mechanics issue references are
retained where previously recorded, but they do not replace the active owners.

| Owner | Remaining scope |
| --- | --- |
| #336 | Printing/errata reconciliation; conditional defaults, prerequisites, TL, aliases, specialties, variable families and technique expansion/policy. |
| #338 | Arts, crafts and trade procedures. |
| #339 | Melee, defense and tactical skill procedures. |
| #340 | Combat technique procedures and parent-specific dispatch. |
| #341 | Knowledge, investigation and professional information procedures. |
| #342 | Medicine and mental procedures. |
| #343 | Physical, outdoor and animal procedures. |
| #344 | Ranged combat skill procedures. |
| #345 | Social skill procedures. |
| #346 | Technology, science and vehicle procedures. |
| #356 | Science, electronics and engineering specialty expansion, split out of #346. |
| #358 | Vehicle movement and combat capability verification, split out of #346. |

Each procedure follow-up lists its exact candidate IDs and must reuse existing
authoritative services. Accounting completion does not certify those procedures.

## Implemented procedures (#346)

`rules/mundane_skills/technology` implements the technology, science and vehicle
group. A procedure is declared once per family and resolved for a specialty
through its family and for a technique through its parent, so a family cannot
drift from its children. Each one supplies a trusted target, typed modifiers, a
repeated-attempt policy and an outcome quantity to a service that already exists;
it never resolves the effect itself, and scoring stays in `gurps_checks`.

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

Boating, Driving, Piloting, Shiphandling, Submarine and Explosives gained their
concrete specialty rows, and the four B233 non-combat techniques of this group
(Motion-Picture Camera, No-Landing Extraction, Set Trap and Work by Touch) now
record a parent-specific default and cap instead of a technique template.

Clearing `runtime-procedure` is the only blocker an implemented procedure
resolves. Contextual blockers stay with #336, every vehicle-control row keeps
`capability:gurps.vehicles.movement` until #358 verifies that capability row, and
thirteen scoped rows are transferred by moving their procedure owner rather than
by guessing their specialties:

| Rows | Procedure owner |
| --- | --- |
| Bioengineering, Biology, Current Affairs, Disguise, Electronics Operation, Electronics Repair, Engineer, Geography, Geology, Hazardous Materials, Mechanic, Paleontology | #356 — their specialty axis is a discipline, not a vehicle class |
| Motion-Picture Camera | #338 — its parent Photography belongs to the arts and trades group |

`unsupported_scope()` publishes every unplayable row with its blockers and their
owners to the scenario, character and LLM validators.

## Validation and runtime contract

All candidates have unsupported status and no runtime hooks. `require_available`
rejects unknown IDs, blocked rows and unsupported definitions even if their
blocker list is mistakenly cleared. Scenario/character/LLM validation therefore
cannot turn catalog presence into playable mechanics.

An implemented procedure changes none of that: `require_available` still refuses
`skill:driving-automobile` and `skill:vacc-suit`.
`tests/test_mundane_skill_technology.py` pins the procedure expectations against
`tests/fixtures/gurps/mundane_skill_technology.json`, whose targets, margins,
outcomes and unit counts were worked out from the source rules rather than
generated from the services under test.

Reference checks cover defaults, prerequisites, specialty/technique parents,
aliases and source-index targets. Prerequisite, technique and alias cycles fail;
valid reciprocal defaults remain source data. Tests independently assert numeric
suit/crewman/weapon defaults, technique caps and pages, the complete source-index
classes, deletion detection, owner propagation and exclusion transfer integrity.
