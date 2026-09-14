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
  implementation evidence (`implemented` or `verified`), and any bound exhaustive
  source-ledger row independently has a required, reviewed, `implemented` or
  `verified` disposition;
- the latest registered Basic profile declares exactly the frozen required
  capability set and does not silently enable optional rules;
- the profile cannot advertise support while any source, capability or item-level
  blocker remains.

The gate intentionally reports current blockers rather than hiding them behind a
generic hook, a manual ruling or generated narration. Scenario and LLM validators
continue to use the existing exact capability registry; model-authored fallback
mechanics do not satisfy certification.

Source, scope, and fixture blockers are filtered by the exact conformance profile.
The unavailable GURPS Lite artifact therefore continues to block Lite certification
without contaminating the Basic Set report. The shared source-audit command still
reports the combined state when no profile is selected.

## Current status

The frozen Basic Set profile is certified: all 73 required capabilities and all
source-ledger and item-level inventory obligations pass the gate. Issue #726
completed the character and social families after #728 completed equipment,
injury, and recovery. Release processes making this claim must still run the
command above and publish its report so regressions fail closed.
