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
