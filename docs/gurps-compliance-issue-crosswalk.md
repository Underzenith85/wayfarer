# Local execution/audit to GitHub crosswalk

Read-only snapshot: last green `artifacts/compliance/b348-quick-contests-checkpoint/basic-set.json`, its execution bindings in `src/wayfarer/certification/basic_set_audit/executable-evidence.json`, capability manifest `src/wayfarer/engine/rules/conformance.py`, source rows `basic_set_audit/sections.json`, and the read-only GitHub issue snapshot dated 2026-10-25. Issue states below have not been refreshed on 2026-10-29. No issues were edited; checkpoint publication is recorded in Git history, and issue closure is not proof that this checkout implements every source exception.

## The exact 15 verified execution obligations

The first nine rows are the **nine verified capabilities out of 73**. The final six are independently verified source sections, not six more capabilities. Friendly labels below expand the exact IDs; capability declarations themselves have IDs rather than separate title fields.

| Exact execution obligation ID | Name | Explicit repository issue mapping |
| --- | --- | --- |
| `capability:gurps.character.skill_defaults` | Skill defaults | Manifest `owner_issue: 98`; #98 closed |
| `capability:gurps.character.skill_difficulty` | Skill difficulty | Manifest `owner_issue: 98`; #98 closed |
| `capability:gurps.character.specialties` | Specialties | Manifest `owner_issue: 98`; #98 closed |
| `capability:gurps.character.techniques` | Techniques | Manifest `owner_issue: 98`; #98 closed |
| `capability:gurps.check.critical` | Critical check outcomes | Manifest `owner_issue: 99`; #99 closed |
| `capability:gurps.check.margin` | Check margins | Manifest `owner_issue: 99`; #99 closed |
| `capability:gurps.check.quick_contest` | Quick Contests | Manifest `owner_issue: 99`; #99 closed |
| `capability:gurps.check.regular_contest` | Regular Contests | Manifest `owner_issue: 99`; #99 closed |
| `capability:gurps.check.resistance` | Resistance rolls | Manifest `owner_issue: 99`; #99 closed |
| `section:campaigns:b348:repeated-attempts` | Repeated Attempts | Audit `historical_owners: [191, 501, 679]`; `completion_owner: null`. No explicit exact closure issue link. #99 is a thematic/parent match, not a recorded section closure owner. |
| `section:campaigns:b349:regular-contests` | Regular Contests | Audit `historical_owners: [191, 501, 679]`; `completion_owner: null`. No explicit exact closure issue link. #99 is thematic. |
| `section:campaigns:b349:the-rule-of-16` | The Rule of 16 | Audit `historical_owners: [191, 501, 679]`; `completion_owner: null`. No explicit exact closure issue link. #99/#754 are thematic, not recorded section closure owners. |
| `section:characters:b017:basic-lift-and-encumbrance-table` | Basic Lift and Encumbrance Table | Audit `historical_owners: [191, 495]`; `completion_owner: null`. Both historical issues are closed. No exact section completion issue exists; #97/#834 are related historical scopes. |
| `section:campaigns:b344:default-rolls` | Default Rolls | Audit historical owners #191/#501/#679; `completion_owner: null`. No exact section completion issue. #98 and #832/#833 are related skill/default and native procedure scopes. |
| `section:campaigns:b348:quick-contests` | Quick Contests, Margin of Victory and Resistance Rolls | Audit historical owners #191/#501/#679; `completion_owner: null`. No exact section completion issue. #99/#754 are thematic contest/resistance matches, not newly closed issues. |

The nine-capability count is the evaluator’s result for its existing explicit case bindings, not an independent claim that every related printed rule is covered. The exact B344 section now has its own independently reviewed source cases and native executable proof. Remaining B173 and individual skill-procedure gaps remain separate; the kernel capability does not certify the whole skill subsystem. Likewise critical classification does not certify every contextual critical consequence.

The source-section titles and historical owners are exact audit fields. The executable-evidence rows bind case IDs but do not supply a GitHub owner. A separate live lookup confirms historical #191, #501, #679 and #495 are closed; none is an exact completion owner for the six section obligations. Historical prose in `docs/gurps-conformance.md` claiming all required capabilities verified/certified is superseded by the fail-closed executed report.

## Remaining measured groups

The report is **not certified**. There are **64 remaining capability obligations**, **1,879 execution blockers**, **97 inventory blockers**, **13 source-ledger blockers**, and **one profile-declaration blocker**. These dimensions overlap; do not add them as distinct missing rules. Many execution blockers are missing or insufficient case bindings, not proof of absent implementation. Every one of the 1,285 source rows has been source-reviewed; source availability is resolved.

The 1,879 execution blockers include 477 skill rows, 463 source sections, 355 traits, 150 supernatural advantages, 100 supernatural spells, 87 modifiers, 64 capabilities and smaller content/equipment groups. Execution blockers usually carry #94 as the report's broad fallback owner. That is an explicit report value, **not** an exact per-defect issue mapping. #742 is the semantic-audit umbrella explicitly named by repository documentation.

The 13 exact source-ledger blockers map as follows (these are explicit `owner_issue` values from the report):

| Exact audit row(s) | Recorded owner | Live state from snapshot |
| --- | --- | --- |
| `section:campaigns:b377:active-defense-options` | #878 | open |
| `section:campaigns:b480:magic-items`; `section:campaigns:b480:enchantment-spells`; `section:campaigns:b481:enchanting`; `section:campaigns:b482:using-magic-items` | #742 | open, umbrella rather than exact consumer acceptance |
| `section:campaigns:b481:interruptions` | #1013 | open |
| `trait:advantage:alternate-form`; `trait:advantage:morph` | #757 | open |
| `trait:advantage:innate-attack`; `modifier:enhancement:cyclic` | #764 | open |
| `trait:advantage:luck` | #854 | open |
| `trait:advantage:unusual-background` | #906 | open |
| `modifier:enhancement:symptoms` | #763 | open |

Additional bounded acceptance fronts are explicitly named in `docs/gurps-compliance-progress.md`: #977 alternative/operating repair tools; #1011 remaining outward/missile Power consumers; #1014 enchanting failure/perversion continuations; #1015 Staff forms/contact; #1016 contextual barehand critical continuation. All are open in the live snapshot. Their bounded local implementations do not close their whole acceptance groups. Other open procedure/conformance work includes #832 physical/outdoor and #833 melee/ranged/techniques; these are thematic coverage work, not exact owners automatically assigned to every new default/minimum defect.

**#1012 is separate:** the repeated permanent Power composition issue is open and the exact selected-source ambiguity is documented in `docs/gurps-repeated-power-source-review.md`. It is not a missing-PDF blocker and must not be combined with #1013 shared-clock implementation gaps or described as resolved by ordinary Power consumption tests.

## Shared fixes versus closed issue history

The current local source review found and corrected real shared behavior beneath historically closed families. The following is a **thematic crosswalk**, except where an explicit documentation/audit link is stated. It does not invent exact defect tickets, reopen issues, claim all acceptance complete, or promote unrelated audit rows.

| Actual local behavior/fix | GitHub relation | Exact-link versus thematic distinction |
| --- | --- | --- |
| B345 final effective-skill floor for voluntary ordinary, approach, physical, combat and staged/long-task checks; historical and mandatory-roll exclusions | #99 closed (success/contest semantics); #98 closed (skill construction/defaults) | No explicit individual closure-owner issue for these newly found shared regressions. #99/#98 are thematic. Physical minimum documentation explicitly calls itself a #1013 follow-up, which does not map all minimum checks to #1013. |
| B349 Regular Contest extreme-score arithmetic and source-section execution; B349 Rule of 16 actual host scope, waived resistance and native ability current modifiers | #99 closed; #754 closed (mental-ability resistance) | No exact section completion owner in audit. #99/#754 are thematic historical scope; the three selected section evidence bindings are separate, exact local obligations. |
| B551 melee attack posture, current target ranged posture, B549/B551 defense posture and B377 retreat corrections, with exact-Basic scope and old-producer compatibility | #835 closed (defense/posture/hit-location numerical verification) | No explicit new-defect issue link. The body of #835 covers defenses/retreat/posture, so this is a thematic family association, not proof it owns every melee/ranged attack exception. #878 remains explicitly open for remaining active-defense consumers. |
| B421 condition attribute propagation, defensive exemptions, drowsiness/irritant producers and current health consequences | #836 closed (damage/injury numerical verification), #763 open (Symptoms) | #836 is thematic historical verification scope. `modifier:enhancement:symptoms` explicitly belongs to #763; not every general B421 condition correction has its own issue link. |
| Source-native corrosion armor loss and repeated Cyclic armor consumption/current-hit consequences | #761 closed; #764 open | `docs/cyclic-attacks.md` explicitly names #761/#764 and retains the partial corrosion/Innate Attack/Cyclic completion boundary under #764. This is an explicit documented family link; no separate exact new regression ticket is recorded. #761 closure does not certify all damage types or modifier combinations. |
| Reverse pending-concentration admission at enchanting Begin/resume; genuine FP-rest/work mutual exclusion; bounded native rest replay; Lift/Jump/Swim off-work clocks | #785 closed; #1013 open | #785 is the explicit historical Enchant/Staff/Power baseline, documented as not granting follow-up acceptance credit. #1013 is the explicit current interruptions/shared-clock owner. Other item consumers remain with #1011/#1014–#1016, and repeated Power source ambiguity stays #1012. |

These regressions are locally recorded in source-review/progress documents and test files, but lack separate exact GitHub defect links where noted. Closed #99/#754/#835/#836/#761/#785 should therefore be described as historical baseline/thematic parents, not as proof the newly exposed behaviors had already been tracked or fully closed.

The separately frozen off-shift one-second Mind Reading candidate is reviewed implementation work under the #1013 theme; it is **not part of this last-green ranged/work-admission report**, creates no additional verified capability/section credit, and does not implement grouped clocks, arbitrary ability families or every timed host.

## Live issue titles and links

GitHub read-only lookup on 2026-10-25; current state is independent of local validation and no issue was changed.

| Issue | Exact title | State |
| --- | --- | --- |
| [#95](https://github.com/Underzenith85/wayfarer/issues/95) | [GURPS] Freeze GURPS 4e scope, rule coverage matrix and conformance fixtures | closed |
| [#98](https://github.com/Underzenith85/wayfarer/issues/98) | [GURPS] Implement skill difficulties, defaults, specialties and techniques | closed |
| [#99](https://github.com/Underzenith85/wayfarer/issues/99) | [GURPS] Implement GURPS success, contest and resistance semantics | closed |
| [#122](https://github.com/Underzenith85/wayfarer/issues/122) | [GURPS] Audit and gate Basic Set coverage across API, UI and LLM play | open |
| [#191](https://github.com/Underzenith85/wayfarer/issues/191) | [GURPS] Complete selected-source audit and exhaustive conformance inventory | closed |
| [#476](https://github.com/Underzenith85/wayfarer/issues/476) | [GURPS] Resolve campaign- and action-scoped conditional skill defaults | closed |
| [#501](https://github.com/Underzenith85/wayfarer/issues/501) | [GURPS] Complete campaign-time administration, awards, knowledge and trap procedures | closed |
| [#679](https://github.com/Underzenith85/wayfarer/issues/679) | [GURPS Audit] Classify remaining Campaigns section-ledger obligations | closed |
| [#742](https://github.com/Underzenith85/wayfarer/issues/742) | [GURPS audit] Close semantic compliance gaps with PR-sized work | open |
| [#743](https://github.com/Underzenith85/wayfarer/issues/743) | [GURPS audit] Require executable evidence for required mechanic certification | closed |
| [#744](https://github.com/Underzenith85/wayfarer/issues/744) | [GURPS audit] Reconcile optional-rule and selected-printing scope declarations | closed |
| [#754](https://github.com/Underzenith85/wayfarer/issues/754) | [GURPS audit] Use source-correct resistance contests for mental abilities | closed |
| [#757](https://github.com/Underzenith85/wayfarer/issues/757) | [GURPS audit] Apply Alternate Form and Morph through character transformations | open |
| [#761](https://github.com/Underzenith85/wayfarer/issues/761) | [GURPS audit] Execute Cyclic attack scheduling and stopping conditions | closed |
| [#762](https://github.com/Underzenith85/wayfarer/issues/762) | [GURPS audit] Execute Cyclic contagion and exposure propagation | closed |
| [#763](https://github.com/Underzenith85/wayfarer/issues/763) | [GURPS audit] Apply and remove Symptoms at cumulative injury thresholds | open |
| [#764](https://github.com/Underzenith85/wayfarer/issues/764) | [GURPS audit] Connect composed purchased attacks to modifier-aware resolution | open |
| [#785](https://github.com/Underzenith85/wayfarer/issues/785) | [GURPS audit] Implement spell effects: Enchant, Staff, Power | closed |
| [#832](https://github.com/Underzenith85/wayfarer/issues/832) | [GURPS audit] Verify skill procedure consequences: physical and outdoor tasks | open |
| [#833](https://github.com/Underzenith85/wayfarer/issues/833) | [GURPS audit] Verify skill procedure consequences: melee, ranged and techniques | open |
| [#834](https://github.com/Underzenith85/wayfarer/issues/834) | [GURPS audit] Add independent conformance cases: attributes, derived statistics and point construction | closed |
| [#835](https://github.com/Underzenith85/wayfarer/issues/835) | [GURPS audit] Add independent conformance cases: combat defense and hit-location numerical cases | closed |
| [#836](https://github.com/Underzenith85/wayfarer/issues/836) | [GURPS audit] Add independent conformance cases: damage types, armor and injury thresholds | closed |
| [#854](https://github.com/Underzenith85/wayfarer/issues/854) | [GURPS audit] Wire B66 Luck into remaining task/combat consumers | open |
| [#878](https://github.com/Underzenith85/wayfarer/issues/878) | [GURPS audit] Implement Acrobatic Dodge, Sacrificial Dodge and Dodge and Drop consumers | open |
| [#906](https://github.com/Underzenith85/wayfarer/issues/906) | Reconcile Unusual Background construction admission and catalog family assignment | open |
| [#977](https://github.com/Underzenith85/wayfarer/issues/977) | [GURPS] Bind alternative and operating toolkit choices to Armoury repairs | open |
| [#978](https://github.com/Underzenith85/wayfarer/issues/978) | [GURPS] Bind defaulted Armoury repair skill to verified training TL | closed |
| [#1011](https://github.com/Underzenith85/wayfarer/issues/1011) | [GURPS] Execute zero-cost outward and missile item effects with Power | open |
| [#1012](https://github.com/Underzenith85/wayfarer/issues/1012) | [GURPS] Resolve repeated Power enchantment composition on an existing item | open |
| [#1013](https://github.com/Underzenith85/wayfarer/issues/1013) | [GURPS] Join enchantment work to split-party clocks and timed off-work activities | open |
| [#1014](https://github.com/Underzenith85/wayfarer/issues/1014) | [GURPS] Execute bounded Quick and Dirty enchantment failure continuations | open |
| [#1015](https://github.com/Underzenith85/wayfarer/issues/1015) | [GURPS] Extend held Staff contact to additional canonical forms and delivery choices | open |
| [#1016](https://github.com/Underzenith85/wayfarer/issues/1016) | [GURPS] Continue contextual barehand critical defense against held Staff contact | open |

No publication, commit, push or merge occurred in this task. The off-shift candidate subsequently passed the frozen 192-file aggregate (4,061 tests plus 15,788 architecture subtests, zero skips), preserving these exact twelve/nine counts and blocker groups; see `artifacts/compliance/offshift-mind-reading-checkpoint/`. Its bounded implementation adds no whole-issue or capability closure.

The subsequent completed default-knowledge/B17 checkpoint passed 4,409 selected tests plus 15,920 architecture subtests, zero skips. It verified the B17 section as the thirteenth execution obligation, preserved nine verified capabilities and reduced blocker records to 1,992 over 1,885 identifiers. No GitHub issue was closed and neither attribute capability was promoted.

Additional live lookup: [#495](https://github.com/Underzenith85/wayfarer/issues/495) — [GURPS] Complete authoritative character creation and background construction procedures — closed.

The subsequent #878 Acrobatic skill-minimum checkpoint passed 4,493 selected tests plus 15,929 architecture subtests, zero skips. It preserves the thirteen/nine counts and all blocker groups. #878 remains open for its explicitly unsupported broader consumers; no issue was changed. The failed stale-envelope run is preserved without credit.


The strict-default/operating-tools/Lifesaving checkpoint passed **5,455 selected tests plus 15,994 architecture subtests, zero skips**, with SQLite and PostgreSQL. The actual gain is the B344 section: **14 execution obligations and nine of 73 capabilities**. Remaining blockers are 1,880 execution, 97 inventory, 13 ledger and one profile declaration (1,991 records/1,884 identifiers). Both B349 sections remain verified; full certification remains false. No GitHub issue was changed or closed.

The source-current Lifesaving minimum is a local regression fix under the broader physical-procedure area: B345 admission and B355 rescue, within the encompassing `section:campaigns:b354:swimming` row (historical owners #191/#501/#679, no completion owner). #832 is a thematic procedure overlap. There is no recorded exact issue for this newly found below-3 defect. Its correction does not close the whole Swimming row or #832. The separately reproduced Diagnosis below-3 defect is queued with independent tests and remains outside this green checkpoint; no exact tracker issue is assigned. The B348 Quick Contests no-defender-attempt work is also isolated; #99/#754 are historical thematic matches, and the exact section has no completion owner.

#977 remains open: this checkpoint verifies its bounded internally timed operating-tool selection, reservations and elapsed-use accounting, while broader alternative/operating contracts remain unsupported. Queued healing, IQ-learning, Staff, paid-item and perversion fixes have no integrated credit here. The source ambiguities #1011/#1012 remain separate from these implementation and evidence gaps. The storage-failed aggregate is preserved without passing credit in `artifacts/compliance/default-eligibility-tools-storage-failed/`.


The completed Medical/Physician checkpoint passed 5,636 selected tests plus 16,077 architecture subtests on both databases, zero skips. It preserves all 14/nine counts and the same blocker groups; full certification remains false. No whole medical/healing section or GitHub acceptance is claimed from this bounded final-score/training correction. Its failed historical-expectation run, repeatability checks and test-only namespace repair are archived. Diagnosis and B348 changes remain outside this green checkpoint.

The subsequent Diagnosis checkpoint passed 5,682 selected tests plus 16,077 architecture subtests on both databases, zero failures/errors/skips. It preserves the same 14 execution obligations, nine capabilities and blocker groups. The voluntary final-score correction is locally implemented and validated; no whole Diagnosis/medical section or GitHub issue acceptance is claimed. B348 remains isolated pending complete source/evidence and integrated validation.

The 2026-10-29 B348 checkpoint passed 6,233 selected tests and 16,156 architecture subtests, zero failures/errors/skips, all sixteen gates. The exact source-section gain is B348 Quick Contests:15 execution obligations/nine capabilities; 1,990 blocker records/1,883 identifiers. The narrow grenade admission correction fixes a preexisting overbroad guard without changing the original tests; it closes no GitHub issue. All issue associations remain as qualified above. The immediate user goal is now a limited campaign with actual end-to-end coverage, not further whole-book certification.
