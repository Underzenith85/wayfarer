# Apportation: private world movement

This bounded #797 implementation is reviewed against the supplied Basic Set
Characters third printing B236, B239 and B251. The admitted Apportation, Haste
and Great Haste baseline evidence is mapped in
`gurps-haste-construction-acceptance.md`; additional Apportation host carriers
remain unverified under [the remaining Movement host carriers (#980)](https://github.com/Underzenith85/wayfarer/issues/980). Private PDFs and source prose are excluded.

Apportation is IQ/Hard, requires Magery 1 and lasts one minute. Listed casting
time defaults to one second. It levitates its subject at Move 1 and cannot damage
subjects with that movement. Living subjects resist with current approved Will.
The ordinary spell ledger owns high-skill reductions, casting interruption,
casting failures, recorded checks, FP payment, cancellation and paid maintenance.
An ordinary successful living cast resolves the canonical resisted-spell Quick
Contest with Rule of 16; caster critical success bypasses resistance, while a
failed caster consumes no resistance dice.

Independent mass tiers are measured in exact millipounds: 1 energy through one
pound, 2 through ten pounds, 3 through fifty pounds, and 4 through two hundred
pounds. Each begun additional hundred-pound increment adds four energy.
Maintenance uses the same base tier. The weight formula is this spell's special
Regular cost rule; there is no second multiplier from Size Modifier. High-skill
reduction follows the tier calculation. Accepted cost and upkeep stay recorded;
increasing the subject beyond its accepted weight allowance refuses movement.

## Canonical consequences and authority

The private service accepts immutable director-authored targeting channels and
measured directed routes between existing connected world locations. Only seated,
trusted directors can author them. Current membership and actor control authorize
caster commands and exact retries. The existing command pipeline owns CAS,
recorded inputs and entropy, atomic reduction, restart and event folding.

Loose physical objects must be canonical world-ground equipment with the same
stable ID as their world object. The pinned equipment specification and current
quantity determine mass; callers cannot supply a cheaper object weight. Readied,
worn, contained, stuck, destroyed or encounter-ground objects are refused.
Container-with-contents movement is not implemented. Moving an eligible object
updates both its actual world location and its canonical ground location without
changing ownership, quantity or condition. Living movement updates the actual
actor world location and retains their inventory. A director-observed body mass
plus current carried equipment determines living weight; unapproved and
transformed-body cases without supported current mass evidence are refused.

A route consumes one shared game-clock second per measured yard. It does not
increase the caster's walking Move or create another clock. Current location,
connection, door state, actor eligibility, spell expiry and mass admission are
rechecked before the canonical change. The existing shared clock guard prevents
skipping unresolved deadlines. No attack or damage command exists on this host.

## Explicit boundaries

This adapter covers noncombat world routes with measured authored distances.
Combat placement and concentration maneuvers require a separate shared adapter;
active encounters containing caster or subject are refused. Actor scene transfers
require their scene transition adapter. Attached fixtures, arbitrary physical
objects without canonical equipment, containers, charged/item casts, transformations
and continuous positional movement are not silently inferred. Weight tiers above
the existing internal spell-energy bound of 100 are unsupported. A moved subject
cannot be remotely controlled across additional locations without refreshed
trusted targeting/route evidence. These limits stay visible rather than expanding
public spell command enums or claiming whole-spell certification.
