# GURPS Fourth Edition conformance baseline

Issue #95 freezes the source boundary and evidence contract for the GURPS mechanics track. It does **not** certify the existing Wayfarer prototype as GURPS-conformant and it does not copy rulebook prose.

## Frozen source artifacts

The selected rules family is GURPS Fourth Edition. The baseline is the pre-2026-revision Fourth Edition line rather than the announced *GURPS Basic Set, Fourth Edition Revised*. Existing page references and expected results therefore remain anchored to the legacy Fourth Edition sources until an explicit migration issue changes this profile.

| Source ID | Artifact | Frozen edition/revision | Printing | Errata policy |
| --- | --- | --- | --- | --- |
| `sjg:gurps-lite-4e-2004` | *GURPS Lite, Fourth Edition* | official 32-page Fourth Edition PDF, 2004 | N/A — electronic edition | official SJ Games corrections/FAQ published through 2026-09-06 |
| `sjg:basic-set-characters-4e-2004` | *GURPS Basic Set: Characters* | Fourth Edition, 2004 electronic edition | N/A — electronic edition | official SJ Games corrections/FAQ published through 2026-09-06 |
| `sjg:basic-set-campaigns-4e-2004` | *GURPS Basic Set: Campaigns* | Fourth Edition, 2004 electronic edition | N/A — electronic edition | official SJ Games corrections/FAQ published through 2026-09-06 |

The official GURPS site identifies Lite as the free 32-page condensation of the system, and the Basic Set source is the Fourth Edition two-volume rules line. SJ Games announced a separate Fourth Edition Revised volume in 2026; that revision is intentionally **out of scope** for this baseline. A future migration must use a new rules profile and new conformance fixture revision rather than silently changing campaigns.

Source URLs are metadata only and are recorded in `tests/fixtures/gurps/conformance.json`. No downloaded copyrighted text is stored in the repository.

## Profiles

`gurps-lite-4e-2004` is the first certification target. Every capability marked `lite_required` must eventually become `verified`; `partial`, `manual`, or `absent` blocks Lite certification.

`gurps-basic-set-4e-2004` is the broader Basic Set target. Every capability marked `basic_required` must eventually become `verified`. Catalog audits may add capabilities; additions are blockers until implemented or explicitly removed from scope in a reviewed profile revision.

The original `package:wayfarer-lite` remains a separate prototype rules package. It is not renamed or treated as either GURPS profile.

## Coverage states

The machine-readable inventory uses exactly four states:

- `absent`: no authoritative implementation exists.
- `partial`: Wayfarer has related behavior, but it is not complete evidence for the selected GURPS mechanic.
- `manual`: the engine can expose or record the concept but does not execute the mechanic authoritatively.
- `verified`: independent source-referenced fixtures and implementation tests agree for the selected profile.

A generic hook, similarly named prototype mechanic, or LLM ruling cannot promote an entry to `verified`.

## Capability IDs and fail-closed behavior

Capabilities use stable dotted IDs under the `gurps.` namespace. `wayfarer.rules.conformance.capability()` rejects unknown identifiers and `require_verified()` rejects every status except `verified`. Scenario validators, character validators, action proposal validation, and future profile APIs must resolve mechanics through this registry (or a generated equivalent) before allowing the LLM to propose them.

This means unsupported mechanics cannot be invented simply because a prompt names them. New mechanics first require a reviewed capability entry, source mapping, implementation owner, and independent expected-result fixtures.

## Fixture contract

`tests/fixtures/gurps/conformance.json` is an independent expectation ledger, not generated from implementation output. Each case contains:

- a stable fixture ID and capability ID;
- a profile and source reference;
- page/section metadata without copied prose;
- explicit numeric inputs and expected outputs;
- units where relevant;
- a rounding rule where relevant;
- a note when current Wayfarer prototype behavior intentionally diverges.

Expected values must be entered from the frozen source by the PR author/reviewer. Tests may consume those values but may never overwrite or derive them from the implementation under test.

## Numeric conventions

Unless a capability-specific fixture says otherwise:

- distance is stored in yards;
- mass is stored in pounds;
- time is stored in seconds;
- monetary values use the rules profile's abstract `$` value as integer units;
- dice expressions are structured as count, sides, and integer add rather than pre-rolled totals;
- fractions remain exact until the source-defined rounding point;
- source-defined rounding is represented explicitly in the fixture as `floor`, `ceil`, `nearest`, `truncate`, or `none`.

Implementations must not introduce implicit banker’s rounding or binary floating-point rounding where the selected rule specifies a different result.

## Updating coverage

Every mechanics PR in #94 must update the inventory and add independent cases for each capability it moves toward `verified`. A capability can be marked `verified` only in the same PR that supplies executable evidence. Catalog audits (#112, #113, #114, #119) may expand the inventory and should open bounded follow-up issues when they discover runtime behavior that does not fit their PR.
