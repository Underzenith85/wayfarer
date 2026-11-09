# Basic Set armor divisors: exact DR arithmetic

The frozen four-shard v2 run passed: 13,378 parent cases, 13,377 ordinary passes, one optional authenticated Codex smoke test skipped, zero failures/errors, 16,202 dynamic subtests (16,202 architecture subtests), and 87.14% combined line/branch coverage. All 22 static checks and four aggregate/accounting/release/provenance commands passed. All 13,283 prior parent IDs, all 95 exact armor additions and all 66 Roadside Watch cases were retained. Actual executable evidence verifies 15 obligations; actual Basic report verifies 9/73 capabilities. Full certification remains false, with 117 source-audit blockers. Zero whole-capability promotions are claimed.

This is a bounded arithmetic checkpoint after the accepted native compliance
checkpoint `61165962e36f1b945c24afda7904bb512e5e6e5c`. Tested armor code is
`6d5ba553517d2c04745b2fbc84d5de6f31817aab`, tree
`b4ef7949c08fd03235606d54a88d68511399cbc4`, on branch
`basic-armor-integration-20261109`. The accepted parent is a documentation-only
successor of tested native code `069310b1`; all prior native behavior and the
Roadside Watch campaign remain regression requirements. The original restored
checkout remains separate. This checkpoint does not claim the unavailable prior
kit work or fresh inspection of the previously uploaded PDFs.

## Exact arithmetic and caller policies

The existing reviewed B378-B379 positive/fractional divisor expectations require
DR division to finish with integer floor before penetration is subtracted. The
old Decimal arithmetic could round prematurely under a caller's low-precision
context: DR 15 divided by 2 must become 7, rather than a rounded quotient changing
the effective armor. The new low-level
[division helper](../src/wayfarer/engine/rules/penetration.py) converts the already
validated finite positive Decimal divisor to its exact integer ratio, then uses
integer multiplication and floor division. It does not depend on ambient
precision, rounding, exponent bounds or Decimal signal flags.

Four existing consumers use that helper:

- Human/location [effective DR](../src/wayfarer/engine/simulation/health/hit_locations.py).
- [Authored object damage](../src/wayfarer/engine/simulation/equipment/objects.py).
- [Affliction penetration](../src/wayfarer/engine/simulation/combat/special_damage.py).
- [Linked ranged follow-up](../src/wayfarer/engine/simulation/combat/ranged/equipment.py).

Their delivery policies remain distinct. Fractional-divisor bare DR 0 handling
retains effective DR 1 for human/object/affliction consumers, while linked
follow-up retains effective DR 0. This checkpoint preserves existing behavior;
it does not reconcile that difference against the source or declare all armor
semantics complete. Input validation remains with the existing callers.

## Actual coverage and persistence boundary

The [new regression module](../tests/test_armor_divisor_exactness.py) expands to
95 parent cases. It uses literal expectations from the existing reviewed
issue-728 matrices, varied Decimal precision/rounding/exponent conditions,
location adjustments and unchanged caller-specific zero-DR policies. It also
checks exact DR before wound subtraction, object damage, affliction resistance
and linked follow-up resolution, plus invalid divisor rejections. Other Decimal
wound/vulnerability arithmetic is outside this patch.

Two cases exercise the actual authored-object `ResourceService` path on SQLite
and PostgreSQL. They check persisted `object_results`, successful exact retry,
conflicting retry refusal without snapshot/history/event mutation, reload and
event folding. They do not establish registered Basic combat, authenticated HTTP
play or original-seed command reexecution for this new armor episode. Prior
accepted replay evidence remains a regression requirement, not a new armor proof.

The integrated focused preflight passed 207 cases: 95 new, 54 hit-location, 35 object,
16 special-damage and 7 heavy-ranged cases, with no failures/errors/skips. That
preflight ran before the metadata-only freeze commit; reviewed source/test bytes
were unchanged through the commit and independently rehashed. It is not described
as a preflight executed at the final commit SHA.

The actual owner inventory contains three fixture variants and 19 broad source
rows (18 required mechanic rows plus one structural row), as well as an equipment
row. Those inherited ledger dispositions are not new verification of 19 mechanics.
Positive/fractional DR arithmetic is covered here. The tight-beam/chinks variant
and broader shared ownership need separate acceptance work. No source-ledger,
expected-result, executable-binding, profile/package pin or public-schema change
is used to obtain credit. Existing reviewed repository expectations are reused;
no new PDF inspection is claimed.

## Validation provenance

The aggregate result above is bound to the frozen source and raw reports in the checkpoint manifest.

The first armor run (`armor-full-20261109-v1`) stopped during static checks:
the unchanged replay-golden checker stalled under a restricted tool context and
was terminated by SIGTERM (subprocess return code `-15`). It did not start the
full shard suite or create the new shard databases. The separate v2 run kept the same code and original golden
fixtures; after the network-permission correction the same checker passed in
6.952 seconds. The interrupted attempt and successful retry are preserved as
infrastructure evidence, not relabeled as a production defect or a waived check.
The other original replay-golden expectations remain unchanged and the checker
passes without rewriting them.

[The checkpoint manifest](basic-set-armor-divisor-checkpoint-2026-11-09.json)
records frozen source hashes, exact static/post commands, the accepted parent,
raw evidence hashes, the interrupted infrastructure attempt and independent
review. Source campaigns use both databases where stated. No new PostgreSQL
original-seed execution is implied.

## Remaining compliance gaps

This batch requests zero whole-capability promotions. Complete armor-divisor
certification still needs the full accepted context, including source review of
the linked-follow-up fractional zero-DR policy and broader owner-row/variant
coverage. Tight-beam/chinks semantics are not newly completed here. Other injury
Decimal arithmetic, wider complete-play/API coverage and the previously recorded
character/self-control/restricted-CP gaps remain separate work. A green aggregate
and inherited ledger labels do not establish full Basic Set compliance.

This records local implementation and validation. Publication, merging and GitHub
issue changes are not performed by this checkpoint.
