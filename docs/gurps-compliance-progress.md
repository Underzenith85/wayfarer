# Basic Set compliance progress

The completion criterion is full compliance with the repository's selected
Basic Set baseline, using the existing deterministic engine, transactional
orchestration and replay architecture. Passing a test batch, registering a
definition or binding evidence does not establish complete rules behavior.

Work is in `/workspace/wayfarer`, branch `work`. Implementation and validation
are authorized; no additional permission to commit, push, merge or publish is
assumed. The working tree contains cumulative, uncommitted implementation work.

## Source availability

The user supplied both selected artifacts again on 2026-10-24. Their complete
SHA-256 digests match the frozen baseline:

| Source | Selected printing | SHA-256 |
| --- | --- | --- |
| Characters | Third, February 2008 | `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e` |
| Campaigns | Fourth, April 2008 | `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80` |

The missing Basic Set source artifact blocker is resolved. PDFs and extracted
book text remain outside the repository. The ledger importer checks both actual
file digests before parsing or creating/replacing ledgers, so another printing
cannot inherit these source identities. The unavailable Lite artifact is a
separate profile and is outside this Basic Set completion criterion.

Reimporting the uploaded artifacts in memory reproduced all 711 section, 487
trait and 87 modifier identities exactly, without replacing their reviewed
dispositions. This validates the denominator, not the rules implementations.

## Measured baseline before this source-review pass

The preceding integrated, uncommitted checkout passed 378 tests with zero skips,
including SQLite and PostgreSQL, plus type, lint, format and contract checks.
Its executed certification report still returned `certified: false`:

| Dimension | Required or unresolved |
| --- | ---: |
| Capabilities | 73 required; 9 verified by executed evidence |
| Inventory | 3,129 required items; 97 inventory blockers |
| Source ledger | 1,044 required rows; 13 ledger blockers |
| Execution | 1,884 blockers across capabilities, inventory and source rows |
| Profile | 1 certification declaration blocker |

Blocker dimensions can overlap; their counts are not a count of distinct rules.
Execution blockers include absent or insufficient evidence and are not a claim
that every affected mechanic is unimplemented. Conversely, existing evidence
can contain an incorrect source expectation and must be corrected when found.
The B348 repeated-attempt section has a separate executed binding and does not
certify the broader success-roll capability.

## Current implementation gap inventory

These are the active bounded owners. The certification report retains the full
item-level inventory; this table describes the current work fronts.

| Owner | Remaining rule or consumer |
| --- | --- |
| #977 | Absent repair equipment, powered-tool fuel/operating reservations, broader alternative qualification and unresolved best-tool TL variants |
| #1011 | Outward zero-cost and missile item Power consumers |
| #1012 | Repeated Power composition; selected-source ambiguity recorded in [source review](gurps-repeated-power-source-review.md) |
| #1013 | Grouped enchanting clocks, active-work casting and timed nighttime travel, physical consumers beyond supported Lift/Jump/Swimming, combat and ceremonial consumers |
| #1014 | Enchantment failure and perversion continuations |
| #1015 | Remaining Staff form/contact consumers beyond supported short forms and standing-human heavy barehand Parry |
| #1016 | Contextual barehand critical continuation |
| #878 | Remaining Acrobatic Dodge skill conditions, aerial/swimming Dodge and Drop and specialized interpositions |
| #757 | Remaining Alternate Form and Morph behavior |
| #764 | Remaining Innate Attack and Cyclic behavior |
| #854 | Remaining Luck variants and source consumers |
| #906 | Conditional/stacked Unusual Background, cross-package benefits and unsupported abilities |
| #763 | Broader Symptoms trait overlays, irritant producers, timing and actual consumers |

Additional source-review implementation gaps remain outside those thirteen
ledger-status blockers. B202 Interrogation still needs five-minute questions,
remaining situational modifiers and nonrevealing answer/claim handling. B347's
numeric critical classifications do not alone demonstrate its GM-determined
critical-consequence handoff. Staged task and voluntary long-task minimum skill
admission is being implemented separately. The certification execution gate
continues to block these broader obligations; metadata availability is not proof
of their completeness.

The 97 inventory-status blockers comprise 90 spells, three supernatural
advantages (Alternate Form, Innate Attack and Morph), and four separate
Luck/Unusual Background/Cyclic/Symptoms rows. These are not 97 independent
behavior defects: shared casting, item and condition consumers can block many
rows, and a partial inventory description does not establish that every concrete
handler is absent. Each row still needs complete source acceptance and an honest
execution binding before promotion.

The 13 unresolved required ledger rows are B377 active-defense options;
B480 magic items and enchantment spells; B481 enchanting and interruptions;
B482 using magic items; Alternate Form, Innate Attack, Luck, Morph and Unusual
Background; and the Cyclic and Symptoms enhancements. Narrow improvements do
not automatically promote any of these rows.

## Source-review milestones

The completed source-review checkpoint includes these bounded corrections:

| Printed rule | Concrete behavior | Evidence |
| --- | --- | --- |
| B345 equipment | Missing/damaged-component penalties apply to basic-or-better tiers; technological improvisation remains −5 | `test_general_equipment_completion.py`, `test_armoury_improvised_tools.py` |
| B349 extreme Regular Contests | Inclusive extreme thresholds normalize the lower score to 10; both scores above 20 scale proportionally with floor rounding | `test_regular_contest_source.py`, independently corrected conformance cases |
| B240 Staff forms | Manufactured baton and short-staff carriers use their actual Shortsword/Smallsword modes and separate physical/spell packets | `test_staff_forms.py` |
| B361/B375 defensive reaction | Current direct skill aftermath modifies the preceding Acrobatics/Aerobatics check | `test_acrobatic_condition_checks.py` |
| B345 voluntary attempts | Fresh authored approaches reject final effective skill below 3 before entropy or effects | `test_noncombat_minimum_skill.py` |
| B345 ordinary actions | Fresh public Inspect/Social commands check the same final-target floor in preview and transactional execution | `test_action_minimum_skill.py` |
| B352/B353/B481 timed rest | Existing Jump and Lift consumers admit their complete source-derived intervals during nightly enchanting rest | `test_enchanting_physical_nighttime.py` |

These are bounded runtime improvements. Their integrated checkpoint is recorded
below; subsequent implementation requires fresh execution fingerprints.

1. Correct source-discovered errors in equipment modifiers and B349 extreme
   Regular Contests, including reviewed expectations that mirrored the errors.
2. Implement bounded actual Staff and Acrobatic Dodge consumers, preserving
   historical command generations, source admission and deterministic replay.
3. Run applicable aggregate checks after integration, including both persistence
   backends. Generate execution evidence only after source and tests stop changing.
4. Reevaluate the certification report and continue with remaining implementation
   and evidence owners. Preserve explicit ambiguity and unsupported behavior;
   never replace missing mechanics with a status promotion.

See [the certification gate](gurps-basic-set-certification.md) for the reproducible
report command and its required execution provenance. Local dirty-checkout
evaluations must be labeled uncommitted; they are not release certification.


## Completed source-review checkpoint (2026-10-24)

The integrated uncommitted checkout passed **1,198 tests and 15,431 architecture
subtests, zero skipped**, across 57 selected files with SQLite and PostgreSQL
configured. Whole-repository mypy passed for 1,515 source files; Ruff and formatting
passed for all 1,777 Python files. The no-explicit-Any gate and injected-defect
quality gates passed. Offline contracts validated 17 operations/45 examples and
28 live messages/12 recovery scenarios. The generated Social v2 snapshot was
reconciled with the earlier removal of the artificial item-Power ceiling; its
only schema changes remove two maximum-100 constraints. Diff checks passed.

Source and equipment audits remain incomplete for their recorded obligations.
The fresh execution report has no malformed or stale bound evidence and verifies
10 obligations: nine capabilities and the B348 repeated-attempt section.
Certification still fails: 9/73 verified capabilities, 1,884 execution blockers,
13 ledger blockers, 97 inventory blockers and one profile blocker. There are
1,995 blocking records over 1,888 distinct obligation identifiers. Overlapping
execution/inventory/ledger obligations are not distinct behavior defects, and
unbound execution is not proof of missing mechanics. Of the execution
identifiers, 103 also have an incomplete inventory or ledger disposition;
the other 1781 lack executed evidence without that additional status blocker.
Those latter dispositions remain declarations requiring behavioral review, not
proof of correct implementation.

Checkpoint digest: `145dd2d8e7601b37640e1cee70b1618f630096574ff0affb261efdf2c49b7ff2`.
Local artifacts are saved under `artifacts/compliance/pdf-source-checkpoint/`:
`basic-set.json`, `pytest.xml`, `test-files.txt`, `source-audit.json` and
`equipment-audit.json`. They describe this uncommitted checkpoint; they are not
release evidence for later edited source. No commit, push, merge or publication
was performed. Coordination explicitly authorizes no push or merge.

Next priority follows shared dependencies rather than blocker-count cosmetics:

1. Preserve purchased prerequisites while live checks recompile approved builds;
   current context loss affects lawful magic and Unusual Background builds across
   ordinary and task consumers.
2. Correct B421 ordinary Per/Will propagation and defensive exemptions for
   supported temporary-attribute conditions, preserving historical command inputs.
3. Implement authenticated corrosion DR erosion and later-hit consumption;
   HP-only corrosion is a behavior defect, not an evidence-only gap.
4. Correct source-discovered retreat posture admission, then extend supported
   delivery/rest consumers using their actual existing architecture.

Repeated Power (#1012) remains a separate source ambiguity. Geometry/producer
joins for aerial or swimming concealment are implementation gaps with clear
rules. Reviewed dispositions, optional-rule decisions and certification
requirements stay intact.


## Completed shared-dependency checkpoint (2026-10-24)

Implemented and verified bounded behaviors:

- B168 purchase-derived prerequisites now reach ordinary Inspect/Social,
  staged/secret checks, long tasks, queued party activities and rulings. Deferred
  execution retains the producer's captured context. See
  [live skill prerequisites](gurps-live-skill-prerequisites.md).
- B421 ordinary IQ penalties propagate to Per/Will and their skills for the
  supported condition sources; defensive attribute exemptions preserve direct
  B361 penalties. Private Symptoms generation 2 preserves generations 0/1.
  See [condition propagation](gurps-condition-attribute-propagation.md).
- B377 retreat rejects sitting/kneeling and permits lying retreat in the existing
  Basic/hex hosts, including legal-choice previews. B549/B551 posture penalties
  now reach both shared defense scorers and live composed/spell/task hosts.
  Independent metadata preserves older inputs and staged defense outcomes.
- B345 voluntary physical skill admission rejects final targets below 3 before
  action dice/cost/time. Mandatory health, survival and involuntary Swimming
  retain their separate behavior. B353 unassisted lifting is now selectable even
  when a purchased Lifting skill is too low to attempt; default route captures
  and bindings retain their prior bytes.
- Individual Swimming joins B481 enchanting rest with the complete planned
  interval, actual fatigue/drowning consequences and authenticated producer
  lineage for hazard replay. Other rest routes and grouped clocks remain open.
- B240 charged-hand Boxing/Karate punches reuse ordinary physical damage and
  separate spell injury, with current canonical purchase and hand eligibility.
- B379 single eligible torso armor loses DR after corrosion's current-hit
  protection is scored. Shared projections apply the item's authenticated loss
  to later attacks and Cyclic ticks, including after the wearer's actual death
  without inventing dead HP injury. HP repair does not erase erosion. See
  [bounded corrosion](gurps-corrosion-armor.md).

Independent review found and fixed a critical-roll discard in the new corrosion
path. Original checks/table chronology now persist in a blocked pending attack;
retries, alternate commands and Luck reopening cannot reroll them. Native normal
critical-hit rows 9–11 resolve normally; other captured outcomes use explicitly
labeled GM adjudication and remain outside native-completion claims.

Aggregate validation: **2,151 tests and 15,532 architecture subtests passed, zero
skipped**, across 99 selected files with SQLite and PostgreSQL configured. The
runtime/test/runner digest remained unchanged throughout the run. Mypy passed
all 1,540 configured source files; lint, formatting (1,809 Python files),
no-explicit-Any, injected-defect quality gates and diff checks passed. All six
schema generators pass `--check`. Offline contracts validate 17 operations/45
examples and 28 live messages/12 recovery scenarios. Authoring and scenario
snapshots now also reflect the earlier source-correct removal of two maximum-100
item-Power constraints in each snapshot; no private runtime fields were added to
published schemas. This selected regression run does not measure full-repository
coverage.

**Certification promotions: 0.** No capability, inventory or source-ledger status
was promoted from unsupported/partial to verified. The fresh report still
verifies ten execution obligations and nine of 73 capabilities. It reports
1,884 execution, 97 inventory, 13 ledger and one profile blocker, totaling 1,995
records over 1,888 identifiers. Source and equipment audits remain incomplete.
The new runtime behaviors live inside still-incomplete broader obligations;
passing tests do not substitute for their remaining rules or evidence.

Checkpoint digest: `37fd0ac44a196d2e637cb97f85df3808a23c764ba488fa64866e3f7c085101c5`.
Artifacts: `artifacts/compliance/shared-dependencies-checkpoint/`, with the same
five artifact names as the preceding checkpoint. These are uncommitted local
results, not release certification. No commit, push, merge, publication or
external deployment was performed.

Next implementation priorities are queued/ruling minimum-skill admission;
identical drowsiness-source deduplication and supported self-control penalties;
and source-native corrosion critical-hit effects beyond rows 9–11. Additional
clear-rule implementation gaps include wand zero-physical-damage contact, attack
posture joins, square retreat destinations, actual flight hosting/altitude-aware
sight, and the remaining enchanting-rest/group-clock consumers. Repeated Power
(#1012) remains a separate source ambiguity. These priorities preserve the
original completion scope and reviewed dispositions.

## B349 closure batch in validation — 2026-10-25 UTC

The bounded acceptance target is exactly
`section:campaigns:b349:regular-contests`; see
[its acceptance criteria](gurps-regular-contest-section.md). Fifteen independently
reviewed source cases cover repeated undecided rounds, extreme-score thresholds,
adjacent boundaries, effective modifiers and reversed ordering. A live pin proof
requires SQLite and PostgreSQL, exact retries/restart, event folding and separate
original-genesis seeded reexecution. Missing, failed, skipped or stale backend
evidence must block this section. Parent chapters and skills receive no implied
credit. Independent review passed the focused 179-test suite, and the evidence
and source-audit tests passed 77 cases. Final integrated execution is pending;
no certification-count improvement is claimed yet.

Shared behavior implemented in this batch includes queued/ruling minimum skill
admission; deduplicated identical Drowsy sources and supported trait self-control
penalties; source-native torso corrosion critical-hit outcomes; B551 melee
attack postures; charged-hand Boxing/Karate contact; and voluntary combat
minimum-skill admission before entropy, including current response conditions,
health checks and due mana refunds. Historical producer generations retain
their scoring. These changes remain inside broader incomplete obligations.

Source re-review also corrected current Interrogation to Quick Contests and
player-versus-player Gambling to Regular Contests. B202 question timing, missing
situational modifiers and fact-disclosure secrecy remain open; historical
Interrogation Regular Contests supply compatibility evidence only. Repeated
Power composition #1012 remains a separate source ambiguity.

The next bounded candidate is B349 Rule of 16, subject to source review of its
resistance eligibility and actual host evidence. B240 wand contact remains an
implementation gap requiring a real zero-physical-damage attack contract.
All work remains local and uncommitted under the original full-compliance goal.

## Source-sections checkpoint — 2026-10-25 UTC

**Two previously unbound execution obligations are now verified:**
`section:campaigns:b349:regular-contests` and
`section:campaigns:b349:the-rule-of-16`. Independent selected-source review and
the fresh executed report support these exact sections; parent chapters and
broader skill, combat and supernatural capabilities receive no implied credit.
No inventory, ledger or capability implementation status was promoted.

Regular Contests requires all fifteen source cases plus actual pin consequences
and replay on both stores. Rule of 16 requires all fifteen of its source cases,
including printed boundaries, offered-but-waived resistance, actual ability
commands on both stores and the trusted pure creature reducer. Current host
classification uses authoritative living-or-sapient facts; unsupported anatomy
and unknown nonsapient biology refuse before response entropy. Historical
source generations and raw ability receipts retain their bytes and seed replay.
Delegated principals, including colon-containing IDs, reexecute with their actual
recorded authority. The creature proof does not claim a nonexistent production
creature orchestration host. See [Regular Contests](gurps-regular-contest-section.md)
and [Rule of 16](gurps-rule-of-16-section.md) for exact acceptance.

The aggregate passed **3,149 tests and 15,663 architecture subtests, zero skipped**,
across 134 selected files in four disjoint shards with SQLite and PostgreSQL
configured. All four shards passed and the runtime/test/runner fingerprint
remained unchanged. Mypy passed all 1,567 configured sources; lint and formatting
(1,844 Python files), no-explicit-Any, injected-defect quality checks and diff
checks passed. All six schema checks pass on the unchanged production models.
Contracts validate 17 operations/45 examples and 28 live messages/12 recovery
scenarios. This selected regression does not measure full-repository coverage.

Integration exposed and repaired GURPS policy leaking into the prototype
adapter. Exact Basic profile gates now retain prototype and Lite behavior for
attack admission, posture, defense and retreat. Actual profile matrix and seeded
prototype proofs supplement retained Basic behavior. A manual replay fixture now
uses recorded semantics; obsolete ability-envelope assertions now check both the
new typed envelope and exact legacy input, plus original-state registered replay.
These repairs preserve behavioral and authority assertions. Failed candidate
artifacts remain separately recorded; they earned no certification credit.

| Final report dimension | Result |
| --- | ---: |
| Verified execution obligations | 12 (previously 10) |
| Verified capabilities | 9 / 73 (unchanged) |
| Execution blockers | 1,882 |
| Inventory blockers | 97 |
| Ledger blockers | 13 |
| Profile declaration blockers | 1 |
| Total blocker records / distinct identifiers | 1,993 / 1,886 |

**Full Basic Set certification remains false.** The inventory still has 3,129
required items and the source ledger 1,044 required rows. Basic source fixtures
now contain 193 reviewed cases; all 101 pending fixtures belong to the separate
Lite profile. The manifest binds 100 source cases to twelve explicit obligations.
Only the two exact section execution blockers were removed; reviewed metadata
alone never establishes completeness.

Checkpoint fingerprint:
`36db5c5c157f2cbf919e5efe1a70bcbd95b7aa64290897ff36dc2b46ada03c15`.
Artifacts: `artifacts/compliance/source-sections-checkpoint/` includes the report,
combined JUnit, selected-file list, source/equipment audits, shard reports/logs and
check summary. This is local, uncommitted evidence. No commit, push, merge,
publication or deployment was performed.

Next is the independently reviewed staged/secret and long-task minimum-skill
slice, still isolated during this checkpoint. Subsequent clear shared gaps
include ranged target-posture modifiers and posture timing/state; face-up lying
is not yet represented, so B364 remains open. B347's GM critical handoff and
B202's timing/modifiers/secrecy remain open. Repeated Power #1012 remains the
separate selected-source ambiguity. Work continues under the original full
compliance scope and local-only permissions.

## Staged minimum-skill checkpoint — 2026-10-25 UTC

The independently reviewed staged/secret and phased long-task slice is now
integrated. Fresh Basic producers reject illegal initial targets before action
costs or original/Luck dice, and recheck actual post-clock preparations. Existing
secret producers retain their captured floor policy; already selected original
checks remain immutable. Long-task intake uses the maximum lawful supervision
bonus without inventing a roll. Actual voluntary supervision/worker phases check
their canonical target, while mandatory overtime HT remains outside the floor.

If supervision becomes unavailable after its selected overtime result, its
actual fatigue/Luck consequences remain committed and the worker still makes
any mandatory overtime check. A later illegal worker phase interrupts without
fabricated work credit or lost elapsed time. Private spent-shift records prevent
a same-day retry bypass. Real 18-hour examples, current toxin conditions, legal
critical supervision, interrupted work, old unmarked producers and original-seed
reexecution run on both stores. Player read/event and genesis checks retain
private marker boundaries. See [staged minimum skill](gurps-staged-minimum-skill.md).

**3,235 tests and 15,664 architecture subtests passed, zero skipped**, across
137 selected files and four disjoint shards. The fingerprint remained unchanged.
Mypy passed 1,570 configured sources; lint, formatting (1,848 Python files),
no-explicit-Any, injected-defect quality and diff checks passed. All six schema
checks and both contract validators passed. Source/equipment audit integrity
passed, while completeness remains false. This is selected regression evidence,
not full-repository coverage.

The refreshed report retains both B349 section obligations: twelve verified
execution obligations, nine of 73 capabilities. There were **zero additional
certification promotions** in this batch. The 1,882 execution, 97 inventory, 13
ledger and one profile blocker remain: 1,993 records over 1,886 identifiers.
No inventory, capability or ledger statuses were changed.

Fingerprint: `398a8fd38cc3ff0ff88f78ded81e67d351a8021c75c1bde64813167fec3c581c`.
Artifacts: `artifacts/compliance/staged-minimum-checkpoint/`, including report,
JUnit, audits, shard logs and checks summary. Work remains local and uncommitted;
no publication or deployment occurred.

Next reviews distinguish correct core Broadsword defaults from a reproduced
missing DX default in an installed source-wide Staff definition. A versioned
package migration is being implemented in isolation so old package pins and
approvals retain their identity. The separate isolated ranged-target posture
work addresses only source-clear body-location terms; unresolved height,
visibility and posture-state consumers remain explicit. B344 Default Rolls and
B364 Change Posture receive no premature execution credit.

## Explicit weapon-default revision checkpoint — 2026-10-25 UTC

The source-wide melee declarations now offer explicit revision2 inheritance
from B208: DX−4/−5/−6 for its 22 Easy/Average/Hard Melee Weapon skills. Lance
is excluded per B204 and retains its independent defaults and Riding
prerequisite. Unarmed No Default declarations remain unchanged. Correct core
Broadsword defaults were already present; the reproduced gap was an installed
source-wide Staff definition.

The production package helper requires an explicit new package version and
authenticates recognized complete default tuples. It preserves all particular
skill edges and admission gates, rejects unknown customized defaults, and keeps
legacy declaration output byte-identical. Existing package pins, configurations
and approvals are not silently upgraded. Actual native Staff now defaults from
DX10 to5 in a newly pinned/approved configuration, with hit/injury, exact retries
and original-genesis reexecution on both stores. Paid, unpaid and forbidden B96
contexts were independently checked through the actual host.

**3,318 tests and 15,681 architecture subtests passed, zero skipped**, across
143 selected files in four disjoint shards; fingerprint unchanged throughout.
Mypy passed 1,573 sources; lint/format (1,852 Python files), no-explicit-Any,
injected-defect quality, diff, all six schema and both contract checks passed.
Audit integrity passed; completeness remains false. This remains a selected
regression, not full-repository coverage.

No certification status was promoted. The fresh report retains twelve execution
obligations, nine of 73 capabilities, and 1,993 blocker records over 1,886
identifiers. **B344 Default Rolls remains open** for general GM/actor Common
Knowledge eligibility and complete literal-source acceptance; fixing declarations
does not supply that missing context.

Fingerprint: `47fdffcbce6ede004c52607396bf55d4d72f9f3f91aa62c6a308a08eb83c2c55`.
Artifacts: `artifacts/compliance/weapon-defaults-checkpoint/`. Work remains local
and uncommitted, without publication or deployment.

The next isolated candidates address ranged target-posture scoring and
enchanting work admission. Review has caught receipt/genesis and final-effective-
skill issues in the former; it is not yet integrated. The enchanting audit
clarified that B481 prescribes an eight-hour workday, not eight hours of quiet
rest or a blanket ban on nighttime casting. Actual concentration and FP-rest
mutual-exclusion gaps are being repaired separately; no fictitious rest rule
is introduced.

## Ranged target posture and work admission checkpoint — 2026-10-25 UTC

The independently reviewed posture and enchanting work/FP-rest slices are now
integrated. Fresh exact-Basic ordinary ranged/thrown, magical missile and ordinary
composed body attacks apply B551's −2 to torso/groin/legs for crouching, kneeling
and sitting. Private launch policy survives Wait, spraying recipients, typed
originals and Luck. Canonical final specifications guard actual composed and
paid missile targets below3 before release/response entropy; a refused release
retains its paid charge. Later spray refusal preserves earlier injury, ammunition
and selected dice, and native abandonment spends no invented refund.

Strict private receipts reject boolean/float generations, duplicate identity and
wrong actor ownership; authored genesis cannot seed them. Both actual player
projections and visible event streams hide policy details. Existing original
rolls and old omitted policies retain their semantics. A stale blind Fireball
fixture was corrected through approved DX11: default7 minus the same B394−4
yields lawful3, retaining location gating, timing, consequences and replay.

This is not whole B551/B364 closure. Conditional crawling/lying exposure still
needs authoritative height/elevation/head facts; lowered random/vitals mapping,
suppression exposure and the separate thrown-owner damage pause remain explicit
unsupported boundaries. Area, point, object and Malediction deliveries are not
misclassified as ordinary ranged body aim.

New enchanting work admission checks every participant's actual concentration
and interrupts only their actual FP-rest tasks when work begins now. Legal
off-shift rest is preserved for future starts; overlapping intervals refuse
before entropy. Fresh Medical rest checks its whole native interval before its
resolver and under lock, and captures complete rest facts for narrow replay.
Other medical care is preserved. Literal source tests distinguish new FP5 from
legacy FP6 when old work incorrectly overlaps rest. B481's eight-hour workday is
not an eight-hour quiet-rest requirement or nighttime casting ban. Off-shift
ability admission remains separate. Distinct-controller Medical submissions
remain unsupported by the inherited self-actor contract; no whole-Medical
replay or certification claim follows from the captured-rest adapter.

**3,979 tests and 15,766 architecture subtests passed, zero skipped**, across
189 selected files in four disjoint shards, SQLite and PostgreSQL configured.
The fingerprint stayed fixed. Mypy passed all 1,585 sources; lint/format
(1,866 Python files), no-explicit-Any, injected quality gates, diff, all six
schemas, both contract validators and audit integrity passed. Schema inputs
were unchanged by the final test-only blindness fixture correction. This is
selected regression evidence, not full-repository coverage.

The fresh report still verifies twelve execution obligations and nine of 73
capabilities. **Zero status promotions**: 1,882 execution, 97 inventory, 13 ledger
and one profile blocker remain (1,993 records/1,886 identifiers), and full
certification is false. Both exact B349 obligations continue to verify.

Fingerprint: `7f79a5e54f54cfdcffb48d35ba30dd52a3a52ee7e1203b1d57062ba4b84234c2`.
Artifacts: `artifacts/compliance/ranged-work-admission-checkpoint/`; the failed
stale-fixture run is separately retained without credit. All work is local and
uncommitted. No push, merge, publication or deployment occurred.

Next isolated work adds authoritative GM Common Knowledge eligibility with
explicit actor-policy migration and preserved learned skills/approvals, and
bounded off-shift Mind Reading with safe early shared-clock admission. Both
remain unintegrated pending full source acceptance, review and validation.

## Active isolated acceptance work — 2026-10-25 UTC

The integrated baseline above remains unchanged. Two candidates are isolated:

- B173 societal default eligibility: explicit seated/trusted GM actor-policy adoption and per-skill declarations, shared default-only compiler/runtime context, handwritten physical/hazard and Innate Attack consumers, and independent captured producer policy. Unadopted actors remain explicitly legacy; this does not certify B344 or silently migrate existing approvals. Literal B344 Lockpicking, Interrogation, Rule of 20 and No Default acceptance is being added separately.
- B69 Mind Reading during legal B481 off-shift time: one-second activation and native Wait/AdvanceEnchanting continuation, early common command admission before entropy, authentic resolve/cancel, and strict private producer lineage. Other abilities and unrelated pending clocks remain unsupported. Independent review found marker owner/time/duplicate and genesis-reservation gaps; repairs require fresh regression evidence before integration.

Neither candidate receives certification credit yet. Each must freeze, pass independent review and targeted checks, then remain fixed through the aggregate SQLite/PostgreSQL regression, static/contracts and audit integrity gates. The repeated permanent Power source ambiguity (#1012) remains separate and does not block these implementations.

GitHub crosswalk recorded on 2026-10-25: [exact execution obligations, capability owners, remaining audit groups and live tracker states](gurps-compliance-issue-crosswalk.md). Dedicated issue ownership, thematic scope and historical closed issues are explicitly distinguished; no GitHub issue was created, edited or closed.

## Off-shift Mind Reading checkpoint — 2026-10-25 UTC

The independently reviewed sixteen-file candidate now supports exact-Basic, ungrouped, out-of-combat Mind Reading activation during a legal enchanting off-shift interval. Its native one-second concentration can advance through ordinary Wait or AdvanceEnchanting only up to readiness, then authentic resolve/cancel continues the actual effect. Shared command admission checks precede assessment and locked reduction; six external source/environment callbacks have their own early checks. Producer owner/time/identity, duplicate records and genesis-reservation negatives were independently verified, together with delegated principals, exact retries, original-genesis replay, exact next-shift completion and current final skill 2/3 refusal/admission. Old unmarked producers retain their original semantics.

Other ability families, analysis/maintenance, grouped/combat activation and unrelated clocks remain explicitly unsupported while this marked concentration is pending. No eight-hour quiet-rest rule, blanket nighttime casting restriction or broader ability minimum claim is introduced. This is a bounded implementation under the #1013 theme, not whole-issue closure.

**4,061 tests plus 15,788 architecture subtests passed, zero skips**, across 192 selected files in four disjoint shards, with SQLite and PostgreSQL configured. All shards exited 0 and the source fingerprint stayed unchanged for the 896-second aggregate. Mypy passed 1,589 configured sources; Ruff/format checked 1,871 Python files. No-explicit-Any, injected quality gates, diff, all six schemas, both contract validators and source/equipment audit integrity passed. This is selected regression coverage.

The final report verifies **twelve execution obligations and nine of 73 capabilities**, with both B349 Regular Contests and Rule of 16 still verified. **Zero obligation/status promotions in this batch**: 1,882 execution, 97 inventory, 13 ledger and one profile blocker remain (1,993 records over 1,886 identifiers); full Basic Set certification is false.

Fingerprint: `9d9a6298c3346e3f35eee884d24439e1097d3d4d6ce96eda6ecd861b5a7cb189`. Artifacts: `artifacts/compliance/offshift-mind-reading-checkpoint/`. Work remains local and uncommitted; no issues were mutated and no push, merge, publication or deployment occurred. The [crosswalk](gurps-compliance-issue-crosswalk.md) records exact audit ownership and live issue states.

Parallel isolated work now comprises Common Knowledge adoption and native default consumers (historical #98/#99, thematic #832/#833); one continuously timed internally charged Armoury-tool contract (#977), with reservation and actual elapsed use rather than invented fuel rates; one source-clear remaining Acrobatic Dodge eligibility scope (#878); and complete literal B17 Lift/Encumbrance Table acceptance with native ownership consequences. The B17 section has no exact completion issue; #97/#834 are historical related scopes. All candidates require independent review and frozen integrated checks before receiving credit. Repeated Power #1012 remains the separate source ambiguity.

## Default knowledge and B17 table checkpoint — 2026-10-25 UTC

The independently reviewed actor-policy rollout adds genuine seated/trusted GM societal default facts, strict private receipts, current unpaid-default projection and captured initiating policy across ordinary/combat/deferred/native physical and care consumers. Learned skills, paid default credit, approvals and package pins remain unchanged. This generation is an explicit adoption rollout: unadopted actors remain historical/legacy, and full B173/B344 closure is not claimed. A separate strict-current candidate is isolated. Auxiliary healing and specialized Armoury proof precedence retain the documented boundary investigations.

The exact `section:characters:b017:basic-lift-and-encumbrance-table` obligation now passes its executable gate: all twenty printed ST rows and all five carrying maxima, fractional/rounded BL, exact boundary weights, actual approved builds, native loaded Move/Dodge, and authoritative ownership changes with retry/stale/seed replay. Twenty-nine source fixtures were independently reviewed. This closes one finite source section, not either attribute capability or unrelated lifting/fatigue/physiology rules.

**4,409 tests plus 15,920 architecture subtests passed, zero skips**, across 209 selected files in four disjoint shards, with both SQLite and PostgreSQL. All exits were zero and the checkout stayed fixed for the 1,124-second aggregate. Mypy passed 1,610 configured sources; Ruff/format checked 1,896 Python files. No-explicit-Any, injected quality gates, diff, all six schemas, both contract validators and source/equipment audit integrity passed. The two integration repairs only forward a new engine keyword in a test adapter and update the exact independently reviewed fixture count from 193 to 222; assertions and the 101 pending Lite fixtures were preserved. The initial stale-count test failure is archived without certification credit.

The final report verifies **thirteen execution obligations and nine of 73 capabilities**. Both B349 sections remain verified. The actual gain is **one previously unbound section obligation**; no broad capability, inventory implementation or source-ledger implementation status was promoted. Remaining blockers: 1,881 execution, 97 inventory, 13 ledger and one profile declaration (1,992 records/1,885 identifiers). Full Basic Set certification remains false.

Fingerprint: `b55a5efa21a229eeaabc5ae733b406a03fa0283a5fa977c33b8032f91f085540`. Artifacts: `artifacts/compliance/default-knowledge-b017-checkpoint/`. The B17 source row records historical owners #191/#495 and no completion owner; #97/#834 are related historical scopes, not an invented exact closure issue. The [current crosswalk](gurps-compliance-issue-crosswalk.md) records this distinction. All work remains local and uncommitted, without issue mutation, push, merge, publication or deployment.

Next independent candidates: #878 separate Acrobatic success-roll minimum, including typed Luck/trait and composed-host joins (frozen and reviewed); #977 timed tool operating reservations and fresh planner admission, including native migration cancellation (isolated under repair/review); and strict-current B344 default eligibility and literal native examples (isolated implementation). No candidate was added during this completed aggregate. Repeated Power #1012 remains the separate source ambiguity.

## Acrobatic skill minimum checkpoint — 2026-10-25 UTC

Fresh exact-Basic Acrobatic Dodge now checks the separate Acrobatics success roll’s final effective skill before any new option, defense or Luck entropy. Effective 2 refuses; source 3 and an actual trait bonus crossing 2→3 remain lawful. Ordinary Dodge retains its independent defense rules, including low defense targets. Native typed opponent choices preflight before selecting Luck rerolls, and the composed-defense host captures only its four Acrobatic-specific rules. Paid Fireball refusal preserves the charge and captured attack original; ordinary Dodge can then settle that same attack. Old omitted policy and exposed originals retain their semantics.

**4,493 tests plus 15,929 architecture subtests passed, zero skips**, across 215 selected files in four disjoint shards, SQLite and PostgreSQL. The final source fingerprint stayed fixed throughout the 1,100-second run. Full mypy passed 1,613 configured sources; Ruff/format checked 1,900 Python files. No-explicit-Any, injected quality gates, diff, all six schema checks, both contract validators and source/equipment audit integrity passed.

The initial aggregate is archived separately without credit: three older composed-posture tests expected no combat protocol metadata. The corrected assertions require exactly the four private Acrobatic flags, preserving independent posture, injury, exact dice, retry and replay assertions; all 28 affected focused cases passed. A new test helper’s explicit Any was also replaced with concrete argument types without changing production behavior or assertions. No unrelated protocol flag was enabled to resolve a test failure.

The final report retains **thirteen execution obligations and nine of 73 capabilities**, including B17 and both B349 sections. **Zero additional obligation or status promotions**: 1,881 execution, 97 inventory, 13 ledger and one profile blocker remain (1,992 records/1,885 identifiers). This bounded #878 correction does not close aerial Dodge/Drop, specialized interposition or the whole active-defense ledger. Full Basic Set certification remains false.

Fingerprint: `57f23fd50de3f49bfac34465c81b2a9863fd1756d2b88a873094ffada727988f`. Artifacts: `artifacts/compliance/acrobatic-eligibility-checkpoint/`; uncredited failed run: `artifacts/compliance/acrobatic-envelope-failed/`. Work remains local and uncommitted, without GitHub mutation, push, merge, publication or deployment.

Parallel work continues on #977 operating-tool admission and exact public-intent compatibility, strict-current B344 eligibility with actual specialized GM proof, #1016 owned knockout/recovery followed by a source-clear Staff critical continuation, and a printed paid Fireball-item prerequisite for #1011. The #1011 zero-cost missile lifecycle is a distinct unresolved source application: ordinary casting time cannot simply be retained for an always-on spell. Official SJ Games FAQ/errata reads were denied before content by the network proxy (HTTP 403); no external clarification or baseline overlay was obtained. This is separate from #1012 repeated Power composition; neither blocks other clear implementation.

## Strict default eligibility and operating tools — migration checkpoint

The reviewed strict-current B344 and #977 candidates passed 579 focused tests plus static, contract and audit integrity gates. The first integrated 240-file aggregate stopped with twelve failures across four fresh native combat test files: the scenarios lacked explicit GM societal knowledge for their unpaid Innate Attack defaults. All four shards stopped at their failure limits; their partial execution does not establish aggregate coverage. Raw results are archived in `artifacts/compliance/default-eligibility-tools-migration-failed/`, without certification credit. Two workers own disjoint test-file migrations; source enforcement and historical producer semantics remain required.

The last fully validated checkpoint remains Acrobatic eligibility: 4,493 tests, 15,929 architecture subtests, zero skips, thirteen execution obligations and nine of 73 capabilities. Parallel isolated work continues on #830 voluntary care minimums, #1011 printed paid Fireball item prerequisites and #1016 Staff knockout/critical continuation. Genuine source questions remain separate: #1012 repeated Power and #1011 zero-cost missile lifecycle. Repository `/workspace/wayfarer`, branch `work`; original completion scope remains full selected Basic Set compliance. All implementation is local and uncommitted. No commit, push, merge, publication, deployment or GitHub issue mutation is authorized by coordination.

## Frozen strict-default/tool candidate and remaining workstreams

The independently reviewed exact B344 Default Rolls evidence is now integrated: seven selected-source cases, fourteen native database witnesses, and seven explicit reviewed source-audit dispositions. The total fixture dispositions are 229 reviewed and 101 pending Lite fixtures. Existing capability bindings and prior source-section bindings are unchanged. The candidate adds no manual certification status. Its accepted scope is the printed Default Rolls/Rule of 20/No Default contract, with ordinary skill-success hosts; named lock-opening/interrogation procedures and passive benefits remain separate.

The candidate is frozen at `4660a9767834377716359e0533d5d657fb4b93d5f1dc38f7bea197029b6cd579`: **246 selected files**, four disjoint shards, SQLite and PostgreSQL. All sixteen final static, format, contract and audit-integrity gates passed. Aggregate validation is still running; no obligation promotion or final checkpoint is recorded yet. Further runtime implementation stays isolated.

Current bounded inventory (these are workstreams, not distinct counts to add to the audit blockers):

| Workstream | Observed gap and disposition | Current milestone |
| --- | --- | --- |
| B344 current default eligibility and literal examples; #977 operating toolkit | Reviewed runtime fixes plus explicit native fixture migrations; whole related families remain partial. | Frozen integrated aggregate, including exact B344 evidence. |
| Voluntary medical final minimum, thematic #830 | Missing final2/3 admission and faithful interrupted task behavior, including emergency treatment thresholds. | Reviewed 17-file candidate: 225 owner tests and 130 independent tests, both database thresholds; isolated composition with strict defaults/tool normalization underway. |
| B173/B248 Physician passive benefit, historical skill/healing families | Actual zero-point Veterinary-derived Physician15 wrongly mitigates Minor/Major critical failure. Source-clear behavior defect reproduced on both databases; a lawful numerical default must remain15. | Separate isolated implementation; preserve old producer semantics and exact first-attempt/day behavior. No separate exact GitHub defect owner assigned. |
| #977 good-quality alternative toolkit | Existing pinned untimed +1 tool plus matching GM good-quality fact is explicitly refused for both repair consumers. Unsupported capability, not an accepted wrong numeric result. | Isolated implementation of one exact-skill/plain/untimed shape and single bonus; broader equipment absence and fuel remain unsupported. |
| #1011 paid Fireball item | Printed manufacture and paid 2d use need source-clear prerequisite behavior; future-dated manufacturing evidence was accepted. | Reviewed 11-file paid-only candidate, corrected chronology, 47 native and 187 shared tests; isolated. |
| #1016 Staff bare-contact critical continuation | Owned knockout, physical then held-spell damage and genuine recovery/authority joins need completion. The actual one-second Awaken host already completes and wakes its target before turn advancement; a suspected inactive-slot gap was traced to fixture flow, so no new skip behavior is required. Ordinary combat settlement already skips inactive actors. | Genuine ten-college manufacture/rows3 and18/full-genesis witnesses pass both databases; remaining consumers and strict admission under implementation. |
| #1014 enchantment perversion | Existing failure marker has no bounded native continuation/use consequence. | Read-only source/contract assessment; no broad side-effect implementation or automatic trigger asserted. |

Genuine source/application questions stay separate: **#1012 repeated permanent Power composition**; **#1011 zero-cost missile lifecycle** under the selected always-on item rules. Official FAQ/errata reads were blocked before content by the proxy; no clarification or source overlay was obtained. These questions do not block the clear work above. Other named ledger groups and the overlapping execution/inventory counts remain in the issue crosswalk; a green selected suite alone does not establish full compliance.

## 49-failure disposition and balanced frozen rerun

The 246-file aggregate finished at an unchanged fingerprint with 49 failures, zero errors/skips, and two shards stopped at their failure limits; it receives no certification credit. Each failed node is classified in `artifacts/compliance/default-eligibility-tools-246-failed/failure-classification.json` and its Markdown companion. Independent review matched all 49 XML nodes: 47 lacked canonical/GM eligibility in the intended positive test setup; two exposed a real saved-Armoury retry authorization error-class regression. That production defect is corrected after strict digest/original/schema/command validation; malformed principal types still raise the domain validation error. No authority or replay defect was relabeled as a fixture correction.

All eight groups received independent source/oracle and rejection review. Exact same-weapon Broadsword/Pistol unknown-default cases and paired NPC-trigger known/unknown cases close the final negative-control specificity gaps; NPC refusal audits zero random-draw calls even though the dispatcher handles validation failures. Original skill minima, target1/3, mandatory HT, costs/HP/ammunition, clocks, authorization, exact old inputs and seeded replay remain required.

The final integrated **168-case focused preflight passed with zero skips**, all sixteen static/contract/audit-integrity gates passed, and collection found **5,381 unique nodes across 248 files**. The balanced four-shard aggregate is now running, frozen at `6288987c144d3cbe3118a9efe6a6534f9338e1a47c11e8aa03a807f0d1c2a6ab`. Timing weights use complete prior green checkpoints and explicitly timing-only observations from failed reports; unknown files receive positive fallback weights. No tests are dropped or credited from the failed reports. Source fingerprint, fresh JUnit, all-shard zero-exit, zero-failure/error/skip and executable-provenance gates remain intact. No new certification count is recorded before this aggregate finishes.

Reviewed future candidates remain isolated: Medical17 integration, Physician13 Minor/Major benefit, paid Fireball prerequisite, Staff rows3/18 and a good-quality alternative toolkit. Additional source-clear Great Healing/natural-Physician benefits and a bounded GM-authored enchantment-perversion continuation are under implementation. Primary-attribute triage also found IQ5 paid technological learning, IQ0 construction and explicit optional NFM-pricing gaps; those plans do not change the current candidate or justify a whole capability promotion.


## Complete 248-file failure review — 2026-10-25 UTC

The aggregate above finished: **5,359 ordinary tests passed and 22 failed**, with **15,978 architecture subtests**, zero errors and zero skips. All 5,381 collected ordinary tests ran; no shard stopped early. The checkout remained at `6288987c144d3cbe3118a9efe6a6534f9338e1a47c11e8aa03a807f0d1c2a6ab` throughout the 2,345-second attempt. Its reports and passing static gates are preserved in `artifacts/compliance/default-eligibility-tools-248-failed/`; this failed attempt earns no certification credit. The previous green checkpoint and its thirteen execution obligations/nine capabilities remain the credited baseline.

The remaining failures comprise eight Inspect cases missing explicit Observation familiarity, seven other hazard scenarios missing their actual canonical skill familiarity, one authored attribute-probe scenario, four toolkit diagnostic compatibility expectations, one exact implemented-feature inventory expectation, and one Lifesaving case. Reviewed test setup repairs retain numeric, authority, cost and replay assertions. Observation also has paired unknown-default rejection and lawful same-campaign enrollment controls. The toolkit repair preserves the existing diagnostic wording without relaxing its rejection predicate; the feature expectation adds only the implemented operating-tools bit.

Lifesaving has an additional **real engine defect**, independently reproduced after supplying lawful Swimming familiarity: the voluntary rescue check admits final effective skill 1. B345 requires refusal below 3. The bounded correction is being validated separately from fixture eligibility, with an independent captured minimum policy, refusal before dice or costs, lawful skill 3, exact historical policy behavior, and trusted-context capture for seeded replay. Historical uncaptured context remains an explicit replay limitation. This is not classified as a stale fixture, and current target-1 success is not a source-lawful oracle.

A recursive inventory now accounts for every function and fixture/import dependency in the entire selected suite: 248 files, 1,697 base test functions and 5,381 collected cases. Its conservative default-related closure contains 1,089 functions/3,879 cases; the narrower focused list is not exhaustive proof. The next frozen validation runs whole modules containing all conservative default candidates, changed cases and authorization controls first, then their exact module complement. Both phases must share one unchanged source fingerprint and collectively execute the complete fresh collection once, with fresh reports and no failures, errors or skips. No obligation is promoted before both phases and the aggregate evidence gates pass.

All implementation remains local and uncommitted. Queued healing, Staff, attribute-learning and enchantment candidates remain isolated from this repair checkpoint. No GitHub issue changes, push, merge, publication or deployment are authorized by this work.

The separate follow-up reproduced a voluntary Diagnosis defect on both stores using canonical Diagnosis/TL8 and genuine societal familiarity: IQ8 gives default2 and a native roll of 3 currently succeeds. IQ9/default3 is the lawful control. This is an additional implementation gap outside the 22 failed cases, not source ambiguity or missing fixture eligibility. Its isolated correction must preserve Diagnosis commands already captured under the Lifesaving-only policy; that existing marker cannot silently acquire new semantics. Mandatory HT checks and antibiotic administration remain separate.


## Strict defaults, operating tools and Lifesaving checkpoint — 2026-10-25 UTC

Fresh unpaid defaults now require real current GM societal familiarity, independently of adoption. Canonical edges, prerequisites, training/TL and approvals still apply; knowledge facts cannot grant missing defaults. Captured historical producers retain their exact inputs and behavior. The bounded #977 internal timed-tool contract reserves at start and charges actual elapsed use once at finish or cancellation. Fresh voluntary Lifesaving now refuses final skill below 3 before dice or costs, while the historical unmarked result and accepted retries retain their recorded semantics.

**5,455 ordinary tests plus 15,994 architecture subtests passed, zero failures/errors/skips**, across 250 selected modules and SQLite/PostgreSQL. Exhaustive default-fixture preflight covered 4,570 tests in 207 whole modules; its exact disjoint complement covered 885 tests in 43 modules. All eight shard exits were zero. Each ordinary case ran once in this successful attempt, the final fresh collection matched, and the source remained fixed for the 1,512-second run. Mypy passed 1,632 configured sources, Ruff/format checked 1,923 Python files, and all sixteen static/schema/contract/audit-integrity gates passed.

The prior 248-file failure ledger distinguishes missing fixture familiarity from two authorization error-class defects and the real Lifesaving minimum defect. A later attempt hit a full temporary filesystem; its 525 completed failures were all SQLite disk-full errors, with partial ENOSPC reports retained. That attempt was invalidated without credit. Only redundant installed dependencies in inactive copies were removed. The successful rerun used workspace scratch, verified database storage/read-write health, and a 20-second storage guard; source, frozen candidates and unique evidence were preserved; no test database was deleted during recovery. One formatting-only diagnostic line wrap was also recorded before the final freeze.

The final evidence audit verifies **14 execution obligations and nine of 73 capabilities**. The gain is exactly one previously unbound obligation: `section:campaigns:b344:default-rolls`, supported by seven reviewed source cases and native observations. B17, B348 Repeated Attempts, and both B349 sections remain verified. No broad capability, inventory or ledger implementation status was promoted. Remaining blockers: 1,880 execution, 97 inventory, 13 ledger, one profile (1,991 records over 1,884 identifiers). Full Basic Set certification is false.

Source fingerprint: `d766214779e3637721fa04c0614089790698280a79929f3da9d99efe3b363746`. Checkpoint: `artifacts/compliance/default-eligibility-tools-checkpoint/`; exact local green source/config/docs snapshot: `/workspace/wayfarer-checkpoint-snapshots/default-eligibility-tools/`. Failure archives retain the 22-case source classification and storage evidence. The [crosswalk](gurps-compliance-issue-crosswalk.md) distinguishes exact audit owners, thematic tracker overlaps and untracked local regressions. Work is local/uncommitted: no issue mutation, commit, push, merge, publication or deployment occurred.

Next bounded integration is the independently reviewed Medical/Physician batch, rebased in isolation onto this checkpoint. The separate Diagnosis version 2 correction, IQ-learning fix, further Physician benefits, Staff, paid items and perversion remain queued. Three isolated workers are implementing the complete B348 Quick Contests prerequisite across the kernel/Ability, creature and mental providers, with preserved historical receipts; no section binding is granted until all nine acceptance families and native/evidence gates pass. Repeated Power #1012 and zero-cost missile lifecycle #1011 remain source ambiguities, while unsupported physiology/pricing and individual skill procedures remain implementation or qualification gaps.


## Medical minimum and Physician-training checkpoint — 2026-10-25 UTC

Fresh voluntary medical checks now apply the B345 final-skill minimum to their complete score before new dice, health checks or costs. Accepted tasks that later fall below 3 interrupt without healing, fabricated elapsed time or refunds. Mandatory HT checks retain their own rules. Trusted care context is captured for current original-seed replay; historical uncaptured environment remains explicitly event-fold-only. Minor/Major Healing passive critical-failure benefits require at least one actual Physician point and the required derived level, with correct first-attempt and prior-success rules. Ordinary skill defaults remain available independently.

**5,636 ordinary tests plus 16,077 architecture subtests passed**, zero failures/errors/skips, across 262 selected modules and both SQLite/PostgreSQL. The 463-case medical preflight was expanded to 475 after the repaired missile-generation module was promoted; its exact 5,161-case complement then passed. All eight final shard exits were zero, fresh collection matched, and source stayed fixed for 1,563 seconds. Mypy passed 1,646 sources; Ruff/format checked 1,939 Python files. All 16 static/schema/contract/audit-integrity gates passed.

The first full attempt is retained in `artifacts/compliance/medical-262-failed/`: 5,635 tests passed and one historical-generation expectation failed. Exhaustive source/test inventory found exactly four constructors; production correctly treated absent historical input as all six generations false. The test now states its sixth false value explicitly. Repeated preflight also found a PostgreSQL fixture reusing a fixed replay-campaign ID in the shared schema. That test now uses the existing UUID-namespaced fixture without changing assertions, fixture IDs, original bytes or seeds. Both module orders and four concurrent workers passed 82 checks; all owned schemas were cleaned up and existing public data stayed unchanged. A read-only audit covered 262 modules plus 342 local imported files and found no other fixed-replay PostgreSQL collision. No passing results were reused from failed attempts.

The final audit preserves **all 14 verified execution obligations and nine of 73 capabilities**. This batch adds no obligation, capability, inventory or ledger status promotion. Remaining blockers are unchanged: 1,880 execution, 97 inventory, 13 ledger and one profile declaration (1,991 records/1,884 identifiers). Full Basic Set certification remains false.

Fingerprint: `1005fa9cd3f4220e32b328920d19374eaaa376165d7d17a50234c5ac9c4bc494`. Evidence: `artifacts/compliance/medical-minimum-physician-checkpoint/`. Exact green source/config/docs snapshot: `/workspace/wayfarer-checkpoint-snapshots/medical-minimum-physician/`. All work remains local and uncommitted, without issue mutation, push, merge, publication or deployment.

The independent Diagnosis version 2 minimum correction is the next queued integration. B348 Quick Contests remains isolated: native ordinary comparisons and current resistance implementations have scoped passing tests, while final source-case bindings and a newly reproduced generic-provider Rule-of16 scope correction remain under review. The latter preserves historical providers and applies the current cap from declared attack/resistance/target facts; it does not grant stock non-attacking advantages new attack procedures.


## Diagnosis final-score checkpoint — 2026-10-25 UTC

Fresh voluntary Diagnosis now refuses final effective skill below 3 before dice, attempts or costs; final skill 3 remains lawful. An independently captured version 2 policy applies only to Diagnosis. Historical version 1 Lifesaving, Diagnosis and antibiotic records retain their original behavior, as do mandatory recovery checks and actual antibiotic dosing. Tests cover real Diagnosis defaults, current condition modifiers, authorization, stale inputs, accepted retries and original-seed replay. Historical uncaptured context remains explicitly unsupported for original-seed replay.

**5,682 ordinary tests plus 16,077 architecture subtests passed**, with zero failures, errors or skips, across 264 selected modules and SQLite/PostgreSQL. The 679-case preflight and exact 5,003-case complement ran at one unchanged source fingerprint; all eight shard exits were zero and fresh collection matched. Elapsed time was 1,555 seconds. Mypy checked 1,649 sources and formatting checked 1,943 Python files; all sixteen static/schema/contract/audit-integrity gates passed. Workspace scratch and the storage monitor remained in use.

The audit preserves all **14 verified execution obligations and nine of 73 capabilities**. No new obligation, inventory, ledger or whole medical capability is promoted. Remaining blockers: 1,880 execution, 97 inventory, 13 ledger and one profile declaration, totaling 1,991 records over 1,884 identifiers. Full Basic Set certification remains false.

Source fingerprint: `499dec2d07acfaa990a602413f935cbf73bbcc463cce3a26f9f032ebe1d292e8`. Evidence: `artifacts/compliance/diagnosis-minimum-checkpoint/`. Exact green source/config/docs snapshot: `/workspace/wayfarer-checkpoint-snapshots/diagnosis-minimum/`. Local changes remain uncommitted; no GitHub mutation, push, merge, publication or deployment occurred.

Next is the complete B348 Quick Contests section, including Margin of Victory and Resistance Rolls. Its shared kernel and Ability, Creature and Mental providers remain isolated pending final evidence review, stable bound-case validation and integration onto this Diagnosis baseline. The candidate includes the source-supported current generic-provider Rule of 16 correction while preserving historical receipts; it grants no new stock attack procedures. Repeated permanent Power (#1012) and zero-cost missile lifecycle (#1011) remain separate source ambiguities. Other queued features remain isolated.


## B348 Quick Contests and grenade admission checkpoint — 2026-10-29 UTC

The complete B348 Quick Contests section now has independently reviewed and executed evidence for ordinary contests, all margin-of-victory cases and resisted attempts. Current failed attacks need no fabricated subject roll; ties protect the defender, current Rule of 16 scope follows actual supernatural-attack/subject/resistance facts, and producer generations preserve historical originals and seeded replay. Twenty-seven reviewed source cases bind 41 actual native observations across public and honestly labeled trusted system providers. This does not give stock non-attacking advantages new attack procedures or certify all related traits.

**6,233 selected tests plus 16,156 architecture subtests passed**, zero failures/errors/skips, across 295 modules and SQLite/PostgreSQL. The 1,908-case/91-module preflight and exact 4,325-case/204-module complement retained all earlier tests and all 193 bound nodes. All eight shard exits were zero; final collection and source/configuration/input hashes matched. Elapsed time was 1,613 seconds. Mypy checked 1,667 sources and formatting checked 1,964 Python files; all sixteen static/schema/contract/audit-integrity gates passed. Workspace scratch, verified PostgreSQL storage and a storage guard remained in use.

Expanded consumer review first exposed ten preexisting grenade-host failures: an overbroad thrown-weapon guard rejected a canonical armed-grenade delivery that schedules its delayed payload without opening a damage pause. The unchanged accepted Diagnosis snapshot reproduced the same failure on both databases. The narrow fix admits only an actual, allowed plain `none` defense with a canonical grenade/warhead and unique unexpired same-encounter armed cause. Ordinary thrown damage pauses and original-roll requests without an actual defense choice remain blocked. All fourteen original grenade tests are unchanged; permanent and independent negative controls cover missing warheads, source custody, identity, unsupported defense choices and invalid causes. No fixture was pinned to an older policy to improve results.

The failed attempt is preserved in `artifacts/compliance/b348-294-preflight-failed/`: all 1,677 preflight cases ran (1,667 passed, ten failed), plus 16,150 architecture subtests; the complement did not start. Its baseline controls and per-failure source classification remain intact and receive no credit. Restart recovery also preserved the initial read-only-cache static failure and rebuilt validation tools/plans in durable workspace storage.

The accepted audit verifies **15 execution obligations and nine of 73 capabilities**, preserving all prior fourteen and adding only `section:campaigns:b348:quick-contests`. Remaining blockers: 1,879 execution, 97 inventory, thirteen ledger and one profile declaration (1,990 records over 1,883 identifiers). Full Basic Set certification remains false. Source fingerprint: `e2f6b19402945430fe81ad2d2fe81ee3480d5004a044b9e079e8bf7c5e101d73`. Evidence: `artifacts/compliance/b348-quick-contests-checkpoint/`; exact green source/config/docs snapshot: `/workspace/wayfarer-checkpoint-snapshots/b348-quick-contests/`.

The user has narrowed the immediate goal to a limited campaign with validated actual coverage. Further whole-book certification work is deferred unless required by that campaign. Roadside Watch is being prepared separately: one player controls two fixed human PCs with a GM-controlled NPC; canonical TL3 rules and ordinary melee, Observation, Stealth and basic First Aid are the intended scope. Magic, ranged combat, shields, advanced character options and browser completeness are excluded initially. Complete production setup/play, genuine injuries and care, rare critical outcomes, server-side exclusions, reload and gameplay replay must pass before it is called playable. Its current pure component checks are not end-to-end evidence. No unfinished campaign files are included in this checkpoint.


## Roadside Watch verified API checkpoint

Tested `7713b402f49e9ccbb42edd3227e53a66bf0dc339`; intended branch `work`. Official evidence: 13224 parents / 13223 passes / 1 allowed skip, 16191 architecture subtests, 66 actual API cases on both stores, 22 static/extra gates; original 65 percent coverage gate and unchanged normal-CI scoped release passed. The separate strict global source-completeness gate failed with 117 preserved blockers. Actual source-bound report: 15 verified execution obligations, 9/73 capabilities, full certification=False; this is report output, not automatic credit promotion. Prior accepted24f history and all V2invalid/V4failed evidence remain preserved. Scope 11 supported / 5 partial / 1 unsupported / 1 unverified is a separate local matrix. No fixed-API sequel, browser, magic or setup-seed claim is added.
