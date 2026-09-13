# Authoritative template construction

Issue #498 completes the engine-owned construction procedure selected from
*Basic Set: Characters*, third printing, B258-B263 and *Campaigns*, fourth
printing, B445-B454. Templates remain trusted content that expands into the
existing `CharacterCompiler`; they are not classes, price authorities or a
second activation path.

## Composition contract

- Occupational templates contain mandatory purchases and bounded choices.
  Choices may be required or optional, enforce unique options and may declare an
  exact point allocation. The allocation is checked against the compiler's
  calculated delta, never a caller-supplied price.
- Repeated compatible purchases merge at the highest level. This permits
  customization without stacking duplicate levels. Conflicting options or
  technology levels reject explicitly, and ordinary prerequisites, exclusions,
  source policy and runtime availability still come from the compiler.
- Racial templates are exact bundles. A sub-race includes its parent race and
  may add racial attribute modifiers. Player-authored races are usable only
  when the trusted campaign catalog enables that permission.
- An explicitly omittable racial trait can be removed. The engine calculates
  the removed component through the compiler and applies the source-defined
  inverse-cost “No X” replacement with an owned adjustment record.
- Meta-traits expand to their component purchases. Component identities,
  prerequisites, costs and unavailable runtime hooks remain visible.

Every contribution records whether it is personal, racial, sub-racial,
occupational or meta-trait content. `TemplateCatalog.advance` recompiles the
personal draft against the same catalog digest, roots, choices and omissions so
advancement cannot erase that provenance. Static adjustments live only in the
trusted template definition and are included in the preview digest.

The independent expected-result fixture is
`tests/fixtures/gurps/template-construction.json`. No engine or package
version changes while the profile is prerelease.
