# GURPS Basic Set certification gate

Issue #122 adds the final fail-closed accounting layer for the frozen
`gurps-basic-set-4e-2004` conformance target. This is a release claim gate, not a
second rules engine and not a substitute for the mechanics owned by the linked
implementation issues.

Run:

```bash
uv run --frozen python scripts/certify_gurps_basic_set.py \
  --output artifacts/release/gurps-basic-set.json
```

The command exits nonzero until every required dimension is complete. The JSON
report records the exact registered profile version and digest, frozen source
baseline, required/verified capability counts, required item-level inventory
count and every blocking item with its owning issue.

Certification requires all of the following at the same time:

- the frozen source, errata, scope and fixture audit passes without stale review
  evidence;
- every Basic-required capability in `wayfarer.engine.rules.conformance` is `verified`;
- every Basic-required owner inventory row is source-reviewed and has executable
  implementation evidence (`implemented` or `verified`);
- the latest registered Basic profile declares exactly the frozen required
  capability set and does not silently enable optional rules;
- the profile cannot advertise support while any source, capability or item-level
  blocker remains.

The gate intentionally reports current blockers rather than hiding them behind a
generic hook, a manual ruling or generated narration. Scenario and LLM validators
continue to use the existing exact capability registry; model-authored fallback
mechanics do not satisfy certification.

## Current status

The repository is expected to remain **blocked** while the dependency issues
listed on #122 are open or their coverage records remain incomplete. Green
ordinary engine CI is not a Basic Set certification claim. A release process that
wants to make that claim must run the command above and publish its report.
