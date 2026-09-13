# Authoritative character creation

Issue #495 completes the engine-owned construction procedure selected from
*Basic Set: Characters*, third printing, B10-B31. It composes the existing
`CharacterCompiler`; catalog purchases, trait costs, statistics, skills,
campaign limits, source pins, and build activation still have one owner.

`CharacterConstructionCompiler` adds only construction facts that are not
ordinary purchases. A trusted `ConstructionContext` supplies campaign TL,
starting wealth, organization membership, equipment prices and equipment
grants. An untrusted `ConstructionDraft` supplies physical facts, personal TL,
native language and culture, associated NPCs, identities, requested language
uses and starting-equipment selections.

## Boundaries

- Exact-budget campaigns reject both overspending and an unfinished point
  balance. Relationship prices join the compiler total and retain their typed
  associated-NPC record.
- Personal TL currently must equal campaign TL. A different personal TL rejects
  explicitly until a pinned High TL or Low TL construction is available.
  Technological skill purchases must name the character's personal TL.
- Height, weight, build and adult age are recorded facts. Size Modifier must
  agree with the existing compiled purchase. Child growth and attribute rules
  reject explicitly rather than being inferred from prose.
- The native language and culture seed the existing background projection.
  Each declared spoken or written use is checked against the derived
  comprehension level.
- Wealth derives from the purchased Wealth trait and the trusted campaign
  starting amount. Equipment prices never come from the draft. Purchases are
  debits, while campaign equipment grants are separate zero-cash ledger rows
  with stable provenance.
- Associated NPC frequency, relative power, Contact reliability and Patron
  power are typed. Availability uses the existing recorded 3d procedure.
- Legal identities produce campaign-audience facts. Alternate and secret
  identities produce actor-allowlisted facts, using the same audience check as
  other engine events; unrelated projections cannot receive their names.

The construction revision hashes the approved build revision, exact draft and
trusted context. Cosmetic description is retained but never grants mechanics.
No engine or package version changes while the rules profile is prerelease.

