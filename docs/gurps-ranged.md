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
| Reload | Ready plus `reload_ammunition_id` and `mode_id` advances the catalog's reload timer; partial progress survives interruptions/restarts. Ordinary Ready never creates ammunition. The initial protocol loads up to capacity at completion. |
| Ammunition | Loaded rounds reserve a specific owned inventory stack. Reserved rounds retain weight and cannot be consumed or transferred out from under the load. Firing consumes every declared shot, including misses, exactly once. Empty stacks and exhausted reservations are removed together. |
| Thrown weapons | The individual item leaves active inventory and is retained in `expended_items`; Ready cannot recreate it. Battlefield recovery is not yet exposed. |
| Rapid fire | Basic-only integer shot counts up to the mode's RoF and the supported 100-shot ceiling. RoF bonus, recoil and attack margin determine hits; Dodge margin removes individual hits. Each remaining hit has separate damage dice, DR and injury reduction. |
| Defenses | Firearms permit Dodge; explicitly blockable projectiles and thrown weapons also permit Block. Unsupported defenses, including second defenses, reject before dice. |
| Critical results | Basic ranged critical attacks persist their table roll and pause with `ranged-critical-table`; they never use the melee critical-miss reducer. |
| Evidence | `tests/test_gurps_ranged.py`: numeric thrown/bow/burst fixtures, modifier boundaries, independent per-hit damage, minimum ST, defense filtering, reload interruption/restart, lost-response retries and reservation conservation. |

Coverage is **partial**. #173 tracks ranged critical consequences, projectile
Parries/locations, vision modifiers, advanced reload protocols, thrown-item
recovery and advanced fire modes. Bracing/sights remain with #152. Certification
and generation validators must continue using the capability registry rather
than inferring support from a typed weapon or manual ruling.
