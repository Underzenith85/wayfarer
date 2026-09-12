# Firearm malfunctions (#173)

The optional B407 procedure is enabled only by an explicit `RangedMode.firearm`
in a newly selected Basic Set equipment catalog. Legacy modes omit the field and
retain their existing critical handling. The original conventional protocol supplies
TL5+ repeating/revolver construction, quality, an optional
authored malfunction threshold, and an optional pinned Armoury skill reference.
The enclosing equipment entry must agree on TL. Catalogs must be explicitly
republished and selected; saved weapons are not inferred from names or skills.

Numeric evidence uses Campaigns Fourth Edition, fourth printing, B382/B407.
The selected-printing reconciliation remains #191. Field-level
provenance is recorded in the equipment audit ledger. No production firearm row
is certified by these synthetic runtime fixtures; catalog binding remains #180.

| Procedure | Authoritative behavior |
| --- | --- |
| Trigger | Compare the unmodified attack total with Malf. TL5 starts at 16, TL6+ at 17; cheap is -1, fine/very fine +1. An explicit threshold override models authored maintenance/weapon circumstances. |
| Precedence | Roll the malfunction table once before defense or damage dice. The malfunction replaces critical-miss consequences. |
| Mechanical failure | No shots fire and ammunition remains reserved. The firearm cannot fire until diagnosed and repaired. |
| Misfire | No shots fire. The failed cartridge remains reserved and unusable. A repeating firearm needs diagnosis and clearing; a revolver can advance past it on its next attack. Retiring that cartridge reduces usable ammunition but is not counted as a fired shot. |
| Stoppage | Exactly one shot fires, using the original attack dice without a burst bonus. Ordinary defense and injury still apply; remaining ammunition stays loaded. |
| Diagnose | One Ready and an IQ-based weapon skill roll, or Armoury; Armoury gets +2 for a misfire. Failed diagnosis preserves the failure. |
| Clear | Three Ready maneuvers and a roll. Misfire uses IQ-based weapon skill or Armoury+2; stoppage uses IQ-based weapon skill-4 or Armoury. Failure permits another attempt; critical failure becomes a mechanical problem. |
| Repair | After diagnosis, 3,600 Ready maneuvers represent an hour of repair work; requires the explicit Armoury skill. Success clears the fault, failure permits another attempt, and critical failure permanently disables the weapon. |

Servicing is an internal `TakeCombatTurn` Ready operation selected by
`firearm_service` (`diagnose`, `clear`, `repair`) and `firearm_service_skill`
(`weapon`, `armoury`). Clearing and repair require two usable hands; servicing
requires freedom from grapples.
Servicing cannot also reload/unload. Changing the servicing actor, operation or
skill starts a new attempt; other intervening maneuvers preserve the saved work.
Canceled Wait continuations clear the service selection. Ordinary Ready cannot
erase a fault; reload and unload reject until it is serviced. A destroyed
firearm cannot be used through an alternative weapon mode.

`Item.firearm_failure` follows the same item through inventory changes.
Validation requires an individual explicitly typed firearm, the original causal
event, and a retained reservation for a misfired round. The
`firearm-malfunction-v1` event stores attack and table dice, catalog/mode, scene,
combatants, build revisions, pre-resolution inventory and pools, ammunition load,
and the resulting injury trace. Service rolls are also saved. The existing CAS,
receipts and replay checkpoint commit all consequences atomically; retries do
not reroll, expend ammunition twice, or advance work twice. Authoring and scenario
schemas expose the new metadata; frozen player and tactical-v1 commands remain
unchanged.

`tests/test_firearm_malfunctions.py` covers the numerical boundaries, priority,
single-shot stoppages, mechanical/misfire ammunition retention, cylinder advance,
interrupted clearing, diagnosis, hourly repair boundaries, failed/critical
repairs, restart/retry receipts, replay, and schema/profile guards.

Coverage remains partial. [Low-TL and exotic constructions](gurps-exotic-malfunctions.md)
now provide explicit explosion, grenade, single-use and power-cell protocols.
Production catalog auditing remains #180. [Projectile readiness](gurps-projectile-readiness.md)
and [thrown recovery/catching](gurps-thrown-recovery.md) are also explicit protocols.
None of these changes certify the overall Basic Set or Lite profile.
