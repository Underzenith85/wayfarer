# GURPS Basic Set world and technology context

Wayfarer models technology and inter-world travel as authored campaign rules and
persisted engine state. It does not copy setting prose into executable code.

## Source boundary

- **Campaigns B511-B514:** a realm has a baseline TL, optional field-specific TLs,
  and named technological paths. Catalog equipment remains immutable. A local
  technology project advances one field by one level, requires qualifying skills,
  labor, cooperation, materials, and two years per step.
- **Campaigns B519-B522:** worlds and planes are typed realms containing canonical
  location IDs. Travel is possible only through authored links whose access facts,
  character definitions, equipment, cost, duration, and executable capability are
  explicit. Physical travel moves travelers and their world-owned objects;
  projection leaves their bodies and inventory in place.
- **Characters B22-B27 and B168:** character and purchased-skill TL remain properties
  of the approved build. Runtime procedures consume the destination field TL rather
  than changing a skill or catalog definition.

Descriptions of cosmologies, genres, and alternate histories are campaign content.
Only their declared mechanical TL context and travel links execute.

## Determinism and authority

World-context commands use the campaign revision, durable receipts, and event-backed
outcomes. Resource validation occurs before costs are spent. Reusing a command ID
with another payload is rejected; retrying the same command returns its recorded
result. Unsupported travel methods fail before fatigue, time, or inventory changes.

No engine version is incremented while the project remains prerelease.
