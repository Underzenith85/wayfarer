# Rules catalog contract

Wayfarer separates immutable rule packages from campaign policy. A campaign pins
the edition, each package ID/version/digest, and an exact policy ID/version.
Package digests use canonical JSON and SHA-256, so changed content cannot silently
reuse a pin.

Definitions have stable IDs, provenance, point costs, prerequisites, exclusions,
parameters, mechanical hooks, and an implementation status. Automatic activation
rejects `unsupported`; `manual-adjudication` is visible and does not imply an
implemented effect. Package loading rejects duplicate IDs, missing references,
and prerequisite cycles.

The bundled `package:wayfarer-lite` is original prototype content. It is not a
complete GURPS catalog and contains no reproduced rulebook text. Licensed or
user-supplied reference data must record its rights provenance before import.

Campaign policies independently constrain sources, point/disadvantage budgets,
attribute and skill ceilings, technology level, supernatural access, and starting
equipment. Updating a package or policy requires a new version and explicit
campaign migration; existing campaign pins never float.

Registered [rules profiles](rules-profiles.md) bundle exact pins, a policy and an
optional GURPS conformance target so new campaigns select a profile by ID and
version, and campaigns change profile only through explicit migration.

Definition kinds are `attribute`, `secondary`, `skill`, `trait` and `equipment`.
`secondary` entries (HP, Will, Per, FP, Basic Speed, Basic Move) compile only when
the compiler is created with an exact GURPS conformance profile; see
[GURPS conformance](gurps-conformance.md). The prototype package has none.

Trait definitions may carry immutable `TraitRules` metadata: exact profile ID,
maximum level, self-control applicability, required typed parameters with allowed
values, approved modifier IDs/percentages/exclusions, and required runtime hooks.
Purchases use `amount` for level and optional `trait` options for parameters,
self-control rating and modifier IDs. Model-supplied prices and formulas reject.
Duplicate purchases and modifiers reject; separate approved modifier IDs stack
additively. Prerequisites and exclusions use the existing compiler checks.

Compilation retains typed trait purchases on the immutable build for trusted
runtime consumers. Declared hooks, including `trait.self_control`, must be in the
server-configured `trait_runtime_hooks` set; the default is empty. Binding a hook
is an execution-service responsibility and does not certify conformance. Costs
alone never make an unavailable trait executable. Point legality remains separate
from power approval, and edits to any option invalidate the existing approval.
Existing prototype package digests and option-free build revisions are preserved;
new metadata changes the package digest and requires an explicit version/migration.
No public frozen v1 HTTP contract or built-in campaign profile is changed.

The **authoring and portable-scenario** schema snapshots explicitly add the
optional `Purchase.trait` object and its `TraitOptions` definition in this change.
Old documents serialize identically when no options are present. The frozen
`contracts/v1` gameplay API and its generated TypeScript client are unchanged;
the authoring UI currently passes scenario documents as JSON and gains no trait
editor here (#116). Unknown options still fail schema validation on the server.
