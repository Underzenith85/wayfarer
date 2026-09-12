# Low-TL and exotic malfunctions (#371)

`FirearmSpec.action` explicitly distinguishes muzzleloaders, breechloaders,
repeaters, revolvers, beams, single-use launchers and grenades. No construction is
inferred from a skill or damage label. TL3/TL4 base malfunction numbers are 12/14;
existing TL5+ catalogs retain their previous trigger and servicing semantics.

The B407 table selects an explosion on 15–18 for TL3 firearms and applicable TL4
grenades, breechloaders and repeaters. A TL4 muzzleloader and TL5+ conventional
firearms retain mechanical failure instead. The default malfunction payload is
1d+2 crushing with 2d fragmentation; an explicitly pinned ammunition `warhead`
replaces it. Exploded weapons cannot fire, parry or be repaired through Ready.

Beam stoppages become mechanical failures without firing. Beam misfires retain
charge and do not eject a fictitious cartridge. `power_cell_capacity` binds an
individual cell with explicit `Item.charges`. Loading reserves charges; firing
spends them, including burst costs. An empty cell remains the same physical item
with its weight and ownership. Existing diagnosis/clearing/repair remains the
common service transaction; failed or interrupted work cannot restore charges.

Grenades require a pinned warhead and normal `fuse_seconds`. Misfire/stoppage makes
a permanent dud. Mechanical failure records an extra d6 delay once. Applicable
TL4 explosions occur at the thrower's position immediately. A single-use launcher
stoppage retires the original launcher and its round as a dud, without counting a
fired shot. Successful use also retires the launcher instance.

Blast causes, payloads, positions, fuse dice and deadlines are durable resource
events. Missed projectile centers remain unresolved until a GM declaration.
Thrown grenades follow their recorded ground position or a recovering holder.
Clock advancement stops at a due blast. Combat round time deferred by immediate
blasts is retained until resolution, including multiple blasts from one burst.

`resolve_weapon_explosion` is a GM-only command. It requires every in-range
participant's chosen diving defense, size modifier and cover facts; positive cover
also names protected hit locations. Include bystanders in the encounter. All
positions, responses and exposed durable-object cover/size facts are validated
before dice. The command uses normal CAS and receipts. Invalid declarations do
not consume rolls, and retries after restart return the original result.

The shared reducer applies B414 range attenuation, separate collateral damage
rolls, direct-only armor divisors, B377 diving steps, fragmentation attack margins
and random human hit locations. Cover applies only to declared protected
locations. Actor injuries use the existing wound reducer; durable-object blast and
fragment damage use the existing object reducer. Air, water and vacuum use their
explicit attenuation factors. Resolution persists its inputs and damage evidence.

This does not certify a production equipment table or complete every explosive
combat option. Contact shielding, internal explosions, secondary fires and other
contextual environmental consequences remain outside this protocol. Evidence uses
Campaigns fourth printing B407/B414–415 and the existing B377 defense procedure;
selected-printing reconciliation remains #191 and overall ranged coverage stays
partial.
