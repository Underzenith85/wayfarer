# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` accounts for the Characters skill chapter without making
unimplemented procedures playable. The candidate package is `0.3.0`; no saved
campaign pin or live representative definition changes.

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
| Explicit chapter examples and parent-specific expansions | 11 |
| **Source index total** | **313** |

The combined Combat Art or Sport listing maps to two candidate records. Thus
313 source entries map to **314 records: 286 mundane and 28 transferred** to
#119's inventory. Specialty families remain explicitly blocked where context
or expansion is incomplete. These counts do not claim enumeration of every
possible player-defined specialty.

Index reconciliation rejects missing records, unindexed additions, overlapping
transfers, invalid expansion parents and page drift. It runs when consumers load
the inventory, including the source-certification report. The exclusion transfer
also verifies names, pages and owners against the supernatural catalog.

## Accounting matrix

`python -m scripts.audit_mundane_skills` generates the report from the typed data.

| Accounting group | Rows | Decision |
| --- | ---: | --- |
| Structured candidate definitions | 254 | Unsupported; source/runtime blockers remain. |
| Listing-only records | 32 | 27 technique templates and five variable families. |
| Transferred cinematic/supernatural skills | 28 | Owned by #242/#243 and source audit #191. |
| **Total accounted records** | **314** | **Zero available mundane candidates.** |

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
| `attribute-default` | 188 |
| `skill-default` | 44 |
| `no-default` | 42 |
| `technology-level` | 87 |
| `unexpanded-specialty` | 59 |
| `listing-only` | 32 |
| `required-specialty` | 15 |
| `prerequisite` | 3 |
| `technique` | 2 |
| `optional-specialty` | 1 |
| `alias` | 1 |
| `technique-template` | 27 |

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

Each procedure follow-up lists its exact candidate IDs and must reuse existing
authoritative services. Accounting completion does not certify those procedures.

## Validation and runtime contract

All candidates have unsupported status and no runtime hooks. `require_available`
rejects unknown IDs, blocked rows and unsupported definitions even if their
blocker list is mistakenly cleared. Scenario/character/LLM validation therefore
cannot turn catalog presence into playable mechanics.

Reference checks cover defaults, prerequisites, specialty/technique parents,
aliases and source-index targets. Prerequisite, technique and alias cycles fail;
valid reciprocal defaults remain source data. Tests independently assert numeric
suit/crewman/weapon defaults, technique caps and pages, the complete source-index
classes, deletion detection, owner propagation and exclusion transfer integrity.
