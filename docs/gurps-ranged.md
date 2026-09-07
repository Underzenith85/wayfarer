# Ranged combat

Issue #106 uses the existing CombatService transaction, resource checkpoint,
command receipts, and signed injury reducer. It requires the exact selected
GURPS equipment/statistics profile; prototype campaigns retain their dispatch.
No frozen player-v1 endpoints or payloads are changed.

The numerical baseline is GURPS Lite Fourth Edition, August 2004, pp. 27–29;
Basic Set Fourth Edition (2004), B270, B372–375 and B550. The repository's
first-printing/errata source audit remains pending. These are independently
specified numeric expectations, not a claim of source certification.

| Behavior | Implementation and evidence |
| --- | --- |
| Scene modifiers | GM StartEncounter declares directed distance in yards, speed in yards/second and size modifier. Shots use the speed/range table, maximum and half-damage ranges. Missing declarations reject; movement rejects pending #115's tactical adapter. |
| Weapon selection | Exact trained/default skill, minimum-ST penalty, explicit ranged mode, individual ready item and existing hand/grip validation. |
| Aim and maneuvers | Matching weapon/mode/target gains Acc and up to two extra seconds. Move and Attack uses the worse of Bulk or -2, without melee's cap; ranged All-Out Attack (Determined) gives +1. Other ranged All-Out options reject. |
| Reload | Ready plus `reload_ammunition_id` and `mode_id` advances the catalog's reload timer; partial progress survives interruptions/restarts. Ordinary Ready never creates ammunition. Legacy catalogs retain magazine loading. Basic catalogs may explicitly select `per-round`: each completed timer reserves one round; firing available rounds cancels unfinished round-loading work. |
| Unload | Basic Ready with `unload_ammunition=true` releases a magazine reservation, including interrupted reload progress. It is separate from loading, allowing a later source switch; owned inventory quantities never change. Individual-round unloading rejects pending its own timing protocol. |
| Ammunition | Loaded rounds reserve a specific owned inventory stack. Reserved rounds retain weight and cannot be consumed or transferred out from under the load. Firing consumes every declared shot, including misses, exactly once. Empty stacks and exhausted reservations are removed together. |
| Thrown weapons | The individual item leaves active inventory and is retained in `expended_items`; Ready cannot recreate it. Battlefield recovery is not yet exposed. |
| Rapid fire | Basic-only integer shot counts up to the mode's RoF and the supported 100-shot ceiling. RoF bonus, recoil and attack margin determine hits; Dodge margin removes individual hits. Each remaining hit has separate damage dice, DR and injury reduction. |
| Defenses | Firearms permit Dodge; explicitly blockable projectiles and thrown weapons also permit Block. Basic thrown projectiles also permit armed Parry at -1, or -2 for items of at most one pound, with ordinary repeated-Parry restrictions. Unsupported defense selections reject before dice; bare-handed catching remains unavailable. |
| Locations and vision | Basic living-human called locations and single-projectile random locations use the existing location, armor, grip and lasting-injury reducer. One Eye applies -3 unaimed or -1 after matching Aim. Blind targeting and multi-projectile random locations reject before dice. |
| Critical results | Single-projectile Basic critical hits use B556 body/head damage, DR, major-wound, shock, eye and held-item effects. Lasting head scarring/deafness pauses with `ranged-critical-head-trauma` after the recorded wound. Ranged critical miss results 7/13/16 impose balance penalties; ranged 16 never substitutes melee's fall. Other miss results, critical Parries and critical bursts remain paused. B382 excludes ranged attacks from failure-by-ten critical misses. |
| Critical persistence | `ranged-critical-v1` resource events retain the table/result trace, pre-resolution combatants, equipment catalog, selected mode, build revisions, scene, ammunition load, inventory and pools inside the existing command CAS. Retried commands return the saved receipt. Unresolved contexts do not claim completed consequences. |
| Evidence | `tests/test_gurps_ranged.py`: numeric thrown/bow/burst fixtures, modifier boundaries, independent per-hit damage, minimum ST, defense filtering, reload interruption/restart, lost-response retries and reservation conservation. |

Additional numeric regression evidence is in `tests/test_ranged_followups.py`,
checked against Campaigns fourth printing, B373, B376, B382, B399-400 and
B556-557, and Characters third printing, B147. Those printings do not certify the
frozen first-printing/2007-errata profile. The authoritative errata endpoints
were inaccessible during this change, so the audit gate remains open.

Coverage is **partial**. #173 remains open for complete critical misses and
Parry consequences, critical bursts, lasting critical head trauma, bare-handed
catches, per-projectile random location traces, individual-round unloading,
Fast-Draw, bow draw/hold fatigue, thrown-item battlefield recovery and advanced
fire modes. Bracing/sights remain with #152. Certification
and generation validators must continue using the capability registry rather
than inferring support from a typed weapon or manual ruling.
