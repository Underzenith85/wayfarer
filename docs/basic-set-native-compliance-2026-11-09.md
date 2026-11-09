# Basic Set compliance: exact arithmetic, native give-in and advancement replay

The frozen four-shard run completed successfully: 13,283 parent cases, 13,282 ordinary passes, one optional authenticated Codex smoke test skipped, zero failures/errors, 16,191 dynamic subtests (16,191 architecture subtests). Combined line/branch coverage was 87.13%. All 22 static gates and all four aggregate/accounting/release/provenance commands passed. Actual executable evidence verifies 15 obligations; the Basic report verifies 9/73 capabilities. Full certification remains false, with 117 source-audit blockers. These counts describe distinct evidence dimensions.

[Committed checkpoint manifest](basic-set-native-compliance-checkpoint-2026-11-09.json) records tested source identity, raw artifact hashes, exact commands, counts and compatibility qualifications.

This candidate implements four bounded improvements using the existing engine,
transactional services and replay dispatcher. They are progress toward the full
selected Basic Set baseline. Whole-capability certification remains incomplete;
no whole-capability promotion is approved for this batch.

## Checkout and source boundary

The base is the last published checkpoint,
`b4541f8a8483c09da25acbd950523b8fd726da70`. The candidate was tested in
`/workspace/wayfarer-compliance-20261109` on branch `basic-compliance-20261109`.
The restored original checkout remains at `8ca2757f`; the prior unpublished
`913eb089` and `47aa4dc5` objects/worktrees are not available in this restored
filesystem. They were not overwritten, and this candidate does not claim to
include their standard/Crash Kit implementation or later HTTP kit evidence.
The published Roadside Watch campaign remains the regression baseline.

No new PDF inspection is claimed. The earlier upload references could not be
resolved in this restored execution environment, and the official publisher
download was blocked by network access. This work reuses the repository's
existing independently reviewed B9/B14-B17 and B120-B121 expectations. It does not
change source-review declarations, expected-result ledgers, public command
schemas, profile versions or package pins to obtain certification credit.

## 1. Exact positive Size Modifier pricing

The existing Basic-only pricing service discounts eligible positive ordinary ST
and HP point costs by the recorded Size Modifier percentage, caps the discount
at 80%, and rounds the final price upward once. Nonpositive point costs retain
their original value without a discount. This supported construction context is
positive SM; it is not a declaration that nonpositive SM is illegal elsewhere.

The old Decimal multiplication and division could round before the required
final ceiling. A real compiler example is ST10, SM3 and HP21: the additional
eleven HP cost 22 points before the 30% discount; the exact result is 15.4, which
must cost 16. The actual unchanged `b4541f8a` compiler returned 15 when its
ambient Decimal precision was 2. The new integer ceiling returns 16 independently
of that context. A separate large-integer service test checks lost integer
digits; it does not extend legal character-score ranges.

All seven existing reviewed prices in the
[Size Modifier fixture](../tests/fixtures/gurps/size_modifier_costs.json)
remain unchanged. The
[new pricing regressions](../tests/test_basic_size_cost_compliance.py)
also retain the actual old compiler's ordinary-precision build revision
`bc08445fab527121bf56ccdc40446c2db89ef91f5419bf959d5dcbde35439e05`, HP21/FP10
pool limits and canonical price. The existing profile/forgery/pool tests remain.

[Actual old compiler results](basic-set-native-compliance-checkpoint-2026-11-09.json)
record both the ordinary 16-point result and the erroneous low-precision
15-point result. Ordinary historical prices and that recorded build hash are
preserved. Reproduction of old incorrectly priced builds produced under an
uncaptured, noncanonical ambient Decimal context is not promised.

## 2. Owned arithmetic contexts for four statistics kernels

Basic Lift, encumbrance thresholds, Basic Speed quarter conversion and
encumbered Move now perform their local arithmetic in an explicitly owned
Decimal context with sufficient precision. The existing exact-value formatter
uses the same protection. These sites retain source-defined fractions and
rounding independently of caller precision, rounding direction or exponent
limits, and do not mutate the caller's context. This is not a claim that every
Decimal operation in the entire compiler was audited or changed.

For example, ST6 Basic Lift remains 7.2 pounds rather than rounding prematurely
to 7. The twenty already reviewed B17 load-table rows keep their literal Basic
Lift and five threshold values under precision 1, 2 and 3, rounding up/down and
a restricted caller exponent range. DX12/HT13 still produces Basic Speed 6.25,
Basic Move 6 and Dodge 9; Basic Move 12 reduces to 9 under Light encumbrance and 4
under Heavy encumbrance.

The
[registered-compiler/native advancement tests](../tests/test_basic_attribute_compliance.py)
also exercise the existing B14-B17 attribute prices/defaults and independent
secondary purchases through the registered Basic v11 compiler. They use actual
approved builds; the injury/fatigue setup is trusted reducer genesis, not a new
public creation-through-combat journey.

Native advancement on each real store starts at HP6/10 and FP7/10. An authorized
42-point grant buys ST12, HT12 and HP13. The resulting pools are HP9/13 and
FP9/12, preserving the four-HP and three-FP deficits rather than healing during
recompilation. Restarted retries preserve the accepted input and result;
conflicting retries and forged grants leave the recorded state unchanged.

## 3. Trusted native self-control give-in

The existing source-reviewed B120-B121 expectation says that giving in to a
self-control trigger does not roll dice. The existing director-authorized
native `SocialService` path now accepts that decision through a strict boolean
in trusted `SocialContext`, only for an exact Basic Set self-control command.
It must bind the subject's actual approved purchased disadvantage, including
under historical condition-generation switches. Existing director/member
authority checks remain in place.

The result records a public trigger, without choosing or executing the PC's
behavior. Private proof records the give-in decision, `resisted=false`, no
dice and zero points spent. It does not invent a remaining CP balance or modify
pools, actors, world state or the advancement ledger. Both zero-CP and banked
two-CP cases are covered. No die draws are made; this is not a claim that the
generic transaction has no retained seed metadata.

The
[native give-in tests](../tests/test_native_self_control_give_in.py)
cover authority/identity refusals, unpurchased traits, wrong profile/kind/type,
public/private projections, restart, exact and conflicting retries, event fold
and original-seed replay. The CP2 replay starts before the actual grant and
reexecutes both grant and social records; CP0 reexecutes its one social record.
Historical ordinary-roll captures omit the new default-false field and keep
their original serialized input.

This adds no public HTTP/player decision field, paid-success CP spending or
automatic disadvantage-specific PC behavior. It must not be described as a new
player-facing consent or control interface.

## 4. Original-seed dispatch for native grant and advancement

The official replay dispatcher now handles the existing native `grant` and
`advance` families through their actual typed `GrantPoints`/`AdvanceCharacter`
commands and `AdvancementService`. It requires the exact two-key original
operation/command envelope. Existing retained-input identity, authority,
revision, digest and typed-field validation remains in use; expected output is
not supplied as an input to force a match.

The tests reject nine mutation variants for each command family and each source
database: principal, request ID, revision, input digest, raw command shape,
unknown family, non-object payload, extra command field and stringified command.
Verification copies must remain unchanged and request no fresh clock or entropy.
A recomputed checksum does not make an unsupported envelope valid. A checksum
is not a signature: fully self-consistent malicious rewrites still require
comparison with the authentic recorded event stream and expected state.

The native attribute episode reexecutes the real two-command grant/advance
sequence from original genesis, and the existing prototype grant/purchase path
also uses the official dispatcher. This changes replay dispatch rather than
granting new award or spending authority.

## Historical evidence and verification state

[Owned attribute readiness](basic-set-native-compliance-checkpoint-2026-11-09.json) records
159 focused passes: 132 existing statistics cases, 24 new attribute cases and
three existing advancement cases. The
[actual old producer](basic-set-native-compliance-checkpoint-2026-11-09.json)
used unmodified `b4541f8a` to produce accepted grant/advance records on SQLite and
PostgreSQL. The
[new-reader verification](basic-set-native-compliance-checkpoint-2026-11-09.json)
reopens each twice, retains raw inputs/snapshots/history/stream and reexecutes
two original-seed commands per backend, four total, while refusing fresh sources.

[The self-control handoff](basic-set-native-compliance-checkpoint-2026-11-09.json) retains
its 21-case focused result and 139-case adjacent regression. Actual unchanged
`b4541f8a` ordinary self-control records produced on each backend retain their
inputs, history, events and final snapshots under the new reader, event fold
and original-seed replay. This proof reconstructs one new-reader runtime and
performs two cached retries; it does not claim two distinct restarts or refusal
of all fresh clock access. A clean separate SQLite producer supersedes a
reporter-only JSON serialization failure, whose earlier evidence remains kept.

Stateful source/native checks use both SQLite and PostgreSQL. Official
original-seed execution and adversarial verification copies use the isolated
SQLite verifier even when the authentic source campaign is PostgreSQL. They are
not described as PostgreSQL command reexecution.

The final frozen focused preflight passed 198 cases with zero failures, errors or
skips. All 22 static gates passed. Fresh full collection contains 13,283 parents
across 744 modules, preserving all 13,224 published-baseline parents and adding 59:
14 Size Modifier cases, 24 attribute cases and 21 native give-in cases. The 66
Roadside Watch parent IDs were retained and validated. The full aggregate result above is bound to the frozen source in the checkpoint manifest.

## Explicit remaining gaps

| Area | Remaining boundary |
| --- | --- |
| Primary/secondary capability completion | IQ0/nonsentient construction, purchased zero secondary values (including Basic Move 0), Machine FP and unlisted ST41-99 damage/construction interpretations remain unresolved. These kernels and sample builds do not close either whole capability. |
| Full Size Modifier pricing context | No Fine Manipulators pricing/disposition still needs assessment. The trait is required in the ledger; no existing named optional-rule exclusion removes it. No new exclusion is invented to claim whole-capability closure. Lifting/Striking ST and other traits are not added here. |
| Self-control capability completion | Paid-success CP authority/spending and broader disadvantage-specific consequences remain separate gaps. Native give-in recording alone does not close the capability. |
| CP award allocation | A separately reproduced source-bucket accounting defect can leave banked points 0 but spendable points 1 after an eligible source-bound +1 award and -1 purchase. Historical purchase rows lack explicit source allocation. This batch does not repair or certify that accounting; it needs a compatibility design and independent source-based acceptance. |
| Public complete-play coverage | Native reducer-genesis and service tests are not new authenticated HTTP character-creation/combat/self-control journeys. Roadside Watch remains unchanged. |
| Source/certification evidence | Existing reviewed expectations are reused. No new PDF review, source-ledger disposition change or whole-capability promotion is claimed. The executable-binding manifest is unchanged; existing reviewed Basic statistics assertions now record executable provenance. Actual report: 15 execution obligations verified, 9/73 capabilities verified; full certification false and 117 source-audit blockers remain. |

The requested goal remains full Basic Set compliance, including several genuine
whole-capability closures when the relevant acceptance criteria can be met. This
candidate fixes concrete behavior in those areas but does not claim that goal
has been achieved. Source-review declarations, expected-result ledgers, the
executable-binding manifest and profile pins remain unchanged. Existing reviewed
Basic statistics assertions now emit executable provenance after their behavioral
checks; this does not promote a whole capability. All work is local; no push, merge, GitHub issue mutation or deployment
is authorized by this checkpoint.
