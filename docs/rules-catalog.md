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
