# Infinite Worlds profile boundary (#494)

The generic GURPS Basic Set profile explicitly excludes the Infinite Worlds
chapter at B523-B546. Profile version 10 records the stable content identifier
`gurps.content.infinite-worlds` as excluded, includes that decision in the
profile digest, and advertises the exclusion in the profile title. It retains
version 9's disabled named optional rules and the same package pins. The
prerelease engine version is unchanged.

The selected-source ledger classifies all 53 chapter rows:

| Classification | Rows | Generic profile treatment |
| --- | ---: | --- |
| Setting content | 30 | Excluded; no runtime or package claim |
| Reusable mechanic candidate | 16 | Reviewed and excluded; no generic operation retained |
| Separate-profile content | 5 | Reserved for a future explicit content profile |
| Reviewed structural/reference exclusion | 2 | Excluded as non-executable material |

The reusable-mechanic candidates cover world classification and coordinates,
conveyors and projectors, travel operations and accidents, detection, natural
phenomena, dimensional highways, world-jumping, and timeline shifts. Their
source review does not make them executable. The generic profile registers no
world identity, travel edge, device, transfer, failure, marooning, or detection
operation for this setting.

Runtime access is fail-closed: an unknown or omitted boundary is rejected, the
registered decision rejects access because the content is excluded, and a
hypothetical included selection remains unsupported while no implementation is
registered. This prevents a narrative destination name from creating a world
or travel edge. Generic planar travel remains independently owned by #504.

The certification report lists the stable identifier under `excluded_content`
and blocks any latest Basic Set profile that omits the exact boundary or marks
it included. Existing campaign profile migration, receipts, revision checks,
and replay remain the only path to the new immutable profile selection.
