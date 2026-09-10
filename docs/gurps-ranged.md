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
| Scene modifiers | GM StartEncounter declares directed distance in yards, speed in yards/second and size modifier. Shots use the speed/range table, maximum and half-damage ranges. Missing declarations reject. With #115's selected hex adapter, attacks recalculate distance from current poses; declared-only movement rejects. |
| Weapon selection | Exact trained/default skill, minimum-ST penalty, explicit ranged mode, individual ready item and existing hand/grip validation. Basic catalogs may opt into `rated_strength` for bows/crossbows: damage and ST-multiplied ranges use the weapon rating. Bows above the wielder's effective (fatigue-adjusted) ST reject before dice, including Aim. |
| Aim and maneuvers | Matching weapon/mode/target gains Acc and up to two extra seconds. Move and Attack uses the worse of Bulk or -2, without melee's cap; ranged All-Out Attack (Determined) gives +1. Other ranged All-Out options reject. |
| Reload | Ready plus `reload_ammunition_id` and `mode_id` advances the catalog's reload timer; partial progress survives interruptions/restarts. Ordinary Ready never creates ammunition. Legacy catalogs retain magazine loading. Basic catalogs may explicitly select `per-round`: each completed timer reserves one round; firing available rounds cancels unfinished round-loading work. |
| Unload | Basic Ready with `unload_ammunition=true` releases a magazine reservation, including interrupted reload progress. It is separate from loading, allowing a later source switch; owned inventory quantities never change. Individual-round unloading rejects pending its own timing protocol. |
| Ammunition | Loaded rounds reserve a specific owned inventory stack. Reserved rounds retain weight and cannot be consumed or transferred out from under the load. Firing consumes every declared shot, including misses, exactly once. Empty stacks and exhausted reservations are removed together. |
| Thrown weapons | The individual item leaves active inventory and is retained in `expended_items`; Ready cannot recreate it. Battlefield recovery is not yet exposed. |
| Rapid fire | Basic-only integer shot counts up to the mode's RoF and the supported 100-shot ceiling. RoF bonus, recoil and attack margin determine hits; Dodge margin removes individual hits. Each remaining hit has separate damage dice, DR and injury reduction. A critical attack roll is undefended, and its margin still bounds how many of the declared shots hit. |
| Defenses | Firearms permit Dodge; explicitly blockable projectiles and thrown weapons also permit Block. Basic thrown projectiles also permit armed Parry at -1, or -2 for items of at most one pound, with ordinary repeated-Parry restrictions. Unsupported defense selections reject before dice; bare-handed catching remains unavailable. |
| Locations and vision | Basic living-human called locations and per-projectile random locations use the existing location, armor, grip and lasting-injury reducer. Burst traces retain each hit's location, location dice, external DR, damage and injury; singular fields retain the first hit for compatibility. One Eye applies -3 unaimed or -1 after matching Aim. Blind targeting rejects before dice. |
| Critical results | Single-projectile Basic critical hits use B556 body/head damage, DR, major-wound, shock, eye and held-item effects. Head scarring/deafness uses the shared lasting-injury reducer. Ranged critical misses resolve balance (7/13/16), unreadiness (8/12), drops (9/10/11/14), self-wounds (5/6, including the mandatory one-time ranged reroll), and timed wielding-arm strain (15). Armed thrown-Parry failures use the parrying weapon and defender; their 16 falls prone, while ranged attack 16 only loses balance. A failed Parry still permits incoming damage. Breakage (3/4/17/18 and cheap-weapon drops) requires pinned durability and `critical_breakage` metadata. Resistant weapons get the B556 confirmation roll; a non-break result drops the weapon. A rapid-fire critical rolls the B556 table once and applies it to a single projectile of the burst: that projectile takes the table's damage multiplier, maximum damage, forced major wound, shock, eye redirection and held-item consequences, and the burst's remaining projectiles are ordinary hits at the declared or separately rolled location. One-shot critical-hit consequences (dropped held items and the forced Do Nothing) still apply once per attack. Missing anatomy/grips and unspecified breakage data remain paused. B382 excludes ranged attacks from failure-by-ten critical misses. |
| Critical persistence | `ranged-critical-v1` resource events retain the table/result trace, pre-resolution combatants, equipment catalog, selected mode, build revisions, scene, ammunition load, inventory and pools inside the existing command CAS. New records also retain the complete ordered table-roll chain (including self-hit and breakage rerolls), the roll subject and affected weapon. Retried commands return the saved receipt. Unresolved contexts do not claim completed consequences. |
| Evidence | `tests/test_gurps_ranged.py`: numeric thrown/bow/burst fixtures, modifier boundaries, independent per-hit damage, minimum ST, defense filtering, reload interruption/restart, lost-response retries and reservation conservation. `tests/test_ranged_critical_bursts.py`: burst critical hits, undefended criticals, single-projectile redirection and restart/retry receipts. |

`tests/test_rated_projectiles.py` adds independent Characters third-printing
B16/B270 and Campaigns fourth-printing B378 cases. Half damage starts **at**
the listed 1/2D range, rounding down. Rated crossbows reload in four Ready
maneuvers at or below the wielder's effective ST, or eight at one or two ST
above it. Interruptions and retries preserve progress without duplicating rounds.
At three or four ST above the wielder, reloading rejects pending the explicit
cocking-aid/standing protocol tracked with #286; at five or more it rejects as
impossible. A loaded crossbow retains its rated damage when the wielder tires.
Ordinary bow reload remains two Ready maneuvers; Fast-Draw and draw/hold state
remain #286. Ratings are explicit Basic-only catalog metadata, with listed
damage-table rows validated when the catalog loads. Existing catalogs omit the
new field and retain their existing ST basis and reload timers. To adopt weapon
ratings, publish and explicitly select a new pinned catalog revision; saved
definitions are not inferred or rewritten. Authoring and scenario schemas expose
the new metadata; the frozen player command contract is unchanged.

Weapon skills are not interchangeable here. #344 binds Bow, Crossbow, Sling,
Blowpipe and the seven concrete Thrown Weapon specialties to this dispatch and
declares the exact weapon modes each governs; a mode outside its skill's class,
a skill family used in place of a specialty, and every ranged combat skill whose
procedure is still transferred to an open child issue are refused when the
equipment catalog is built and again before dice. See
[Ranged combat procedures](gurps-mundane-skills.md#ranged-combat-procedures-344).
Bolas and Net are bound too: a landed, undefended throw leaves a pinned binding
on the target that penalises its attacks and active defenses, can reduce Move to
zero, and is shed only by winning a Ready-maneuver contest against the binding's
ST. The TL-indexed Guns and Beam Weapons specialties dispatch a weapon of the
campaign's own era and refuse one from another era. Crew-served weapons, liquid
projectors and launcher-assisted throws remain unsupported; this change does not
widen the permitted fire modes. A B270 rated weapon ST belongs to the launcher its own
skill governs: a rated bow cannot be fired under Crossbow, and a rated
crossbow cannot be fired under Bow.

Additional numeric regression evidence is in `tests/test_ranged_followups.py`,
checked against Campaigns fourth printing, B373, B376, B382, B399-400 and
B556-557, and Characters third printing, B147. Those printings do not certify the
frozen first-printing/2007-errata profile. The authoritative errata endpoints
were inaccessible during this change, so the audit gate remains open.

Coverage is **partial**. `tests/test_ranged_critical_completion.py` adds
independent critical-miss, weapon-quality, per-hit location and restart/retry
fixtures, using Campaigns fourth printing B376, B382, B399 and B556-557.
The existing first-printing/errata audit remains a separate gate (#191).
New quality metadata is opt-in and Basic-only; it requires a durable individual
weapon and does not alter saved catalog definitions. Disabled weapons cannot be
Readied, and broken thrown items retain their condition in `expended_items`.
Legacy weapon definitions have no breakage certification; their existing
ordinary drop semantics are retained, while break results remain paused.

Burst critical hits are implemented here, with independent evidence in
`tests/test_ranged_critical_bursts.py` (Campaigns fourth printing B373, B399,
B556). #173 now supplies [opt-in conventional firearm malfunctions](gurps-firearms.md):
B407 precedence, single-shot stoppages, retained misfires, diagnosis, clearing,
and mechanical repair with persisted consequences and receipts. Catalog auditing
remains #180, low-TL/exotic malfunction variants remain #371; printing reconciliation
remains #191. No malfunction number is inferred from a skill or damage type.
Named follow-ups retain the other required scope:
#286 owns individual-round unloading, Fast-Draw and bow draw/hold fatigue;
#287 owns bare-handed catches and thrown-item battlefield recovery. Those
protocols require additional typed skill, weapon readiness, and ground-item state;
`reload_progress`, `expended_items`, and generic Ready are not substitutes.
#152 supplies typed one-handed and prone-bipod bracing plus fixed/variable scope
timing. Certification and generation validators must continue using the
capability registry rather than inferring support from a typed weapon or manual
ruling. Shotguns, automatic-only minimum bursts, suppression and spraying remain
unsupported; this change does not widen the permitted fire modes.
