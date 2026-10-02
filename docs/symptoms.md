# Symptoms consequences (#763)

The selected Characters third printing B109 defines damage from a particular enhanced attack as cumulative, activates its Symptoms only when that damage exceeds a fraction of the victim's base HP (or FP for fatigue attacks), and removes Symptoms when damage heals past that threshold. Equality therefore has hysteresis: it does not activate a new symptom, and does not remove an already active one. Repeated hits from one attacker/channel share a source; different channels and attackers retain separate debts.

The executable construction currently supports blindness, coughing, and ST/DX/IQ/HT attribute penalties, with source-derived Affliction costs from B35–36 multiplied by three, two, or one for the one-third, one-half, or two-thirds thresholds. There is no additional resistance roll. Attribute penalties change the authoritative runtime build, governing skills, and ST lift/damage; IQ also changes Will and Per. Other secondary attributes retain the printed exception. Concurrent identical attribute penalties use the worst applicable penalty (B35). Blindness blocks visual sensory eligibility; coughing modifies DX/IQ checks and prevents Stealth.

Injury and fatigue reducers retain causal damage debts. Medical care, clock-accrued rest, physiological recovery, sleep and Vampiric Bite reconcile actual positive recovery against those debts. Where source damage does not specify a wound allocation, this engine allocates eligible healing to the oldest outstanding damage debt. Unrelated damage consumes its own allocation and cannot activate another attack's Symptoms. Active Cyclic debt remains protected until its attack stops. The allocation policy is explicit engine bookkeeping; B109 does not prescribe a wound-selection UI.

The stored approved character and purchases remain immutable. Runtime projections supply changed statistics, skills, and eligibility to existing simulation consumers; command receipts retain exact retries and revision/authority checks. Empty Symptoms fields are omitted to preserve existing replay snapshots.

## Attribute checks and defensive reactions

Symptoms attribute penalties affect trained and defaulted governed skills using canonical attribute identifiers (Characters third B36/B109; Campaigns fourth B421). For example, DX−4 changes an ordinary Broadsword target from 13 to 9. Unresolved ordinary checks use current effects, while supported active defenses, Fright Checks and Malediction resistance preserve their approved defensive inputs: the same fixture retains Dodge 9, Parry 10 and Block 10, and IQ−4 leaves a defensive Will target of 10 intact. Attacking with Will still uses the current penalty. Permanent attributes, blindness, fatigue, injury and equipment constraints retain their effects.

The private `symptom_attribute_generation: 1` marker is independent of the casting and targeting generations. New command inputs select the corrected projections. Historical inputs retain their original bytes, hashes and seeded outcomes; exact committed retries retain their receipts. An indexed lookup on SQLite and PostgreSQL distinguishes an absent command from a legacy command with no retained input, without folding the campaign's entire history. New opaque and array inputs retain their exact inner text in a private wrapper, and object inputs also retain their original text. Duplicate lookup validates the complete stored digest and private metadata before comparing exact intent bytes, including early tactical HTTP and migration lookups. Changed parameters and whitespace remain conflicts. Both stores read the receipt and its resulting checkpoint from one snapshot. Replay removes validated metadata before a raw command reaches its strict adapter; digest validation still uses the complete stored input. The command scope resets after exceptions and is isolated across concurrent tasks. Fright and Resource reexecution require their existing trusted service resolvers; the generic offline replay registry does not yet dispatch those families or Ability commands.

`tests/test_symptom_attribute_consumers.py` and `tests/test_symptom_attribute_generations.py` inspect actual rolls, outcomes, recovery, authority, retries and historical inputs. Existing indirect ST/Basic Lift equipment consequences are retained; their broader interactions remain unverified and are not covered by a blanket defensive exemption. These residuals remain owned by #763, whose source row remains partial.

## Personal spellcasting checks

Characters third printing B35–36/B109 and B234–237 distinguish an IQ penalty's effect on the casting roll from the base spell skill that determines ritual, time and energy. An approved skill of 14 with current IQ−2 Symptoms rolls against 12 before other modifiers. The personal casting check reads the active Symptoms penalty immediately before the canonical success roll, including a ceremonial leader (B238) and the private Lockmaster/Magelock host. Onset or recovery during an unfinished cast changes that roll; it does not revise accepted casting cost, duration, required Concentrate turns or ritual tier. Existing condition modifiers still apply once.

Campaigns fourth printing B421 excludes resistance, active defense and Fright checks from temporary attribute penalties. In particular, temporary HT−2 on a Daze subject does not change its approved HT10 resistance target. Item activation continues to use item Power (B482), without subtracting its user's personal IQ loss. The Symptoms attribute modifier is local to personal casting; it is not added to the shared condition-modifier function or to already projected skill consumers. Remembering spells and distraction checks retain their existing behavior.

The private `check_generation: 1` command-input marker selects this casting correction independently of the existing `targeting_generation: 1` marker. Historical commands without the check marker retain their original behavior in seeded reexecution and exact live retries, including automatic completion during the final combat Concentrate turn and the private lock spell host. Completed check targets, dice, margins and outcomes remain immutable. Public commands/events, execution versions and the seven reviewed replay fixtures are unchanged. `tests/test_symptom_casting_checks.py` exercises the check targets and commitment boundaries on SQLite and PostgreSQL; this narrow completion does not promote the whole Symptoms family to certified status.

The existing B345 minimum effective skill for lock casting includes the current IQ penalty. If Symptoms begin after admission and reduce that target below 3, completion rejects before distraction or casting dice and before energy payment. The accepted cast remains cancellable through its existing command without paying casting energy; rejection does not leave an unfinishable transaction or revise prior history.

Other Advantages, Disadvantages, Negated Advantages and Irritant variants require dedicated executable consumers. They are rejected in approved runtime attack construction and remain unsupported. This PR establishes engine behavior for the named variants; it does not certify the end-to-end gameplay gates or promote source/evidence ledger readiness from focused tests.

## Current sight for item casting

Characters third printing B239 and Campaigns fourth printing B482 retain the contained spell's targeting modifiers when item Power supplies its base skill. An active blindness Symptom or two currently disabled eyes therefore adds the Regular spell's separate −5 for a known but unseen, untouched subject. A Power15 Daze item at zero range rolls against 10: a roll of 11 fails, spends the ordinary failure cost of 1 FP, and does not daze the subject. Its accepted 3-FP casting cost and two-second concentration commitment remain intact. Zero range alone is not evidence of touch. Self-casting and a single disabled eye do not acquire this penalty; blindness cannot authorize an otherwise unknown subject.

Both supported execution versions refresh this targeting contribution before an unresolved Regular or Resisted item roll, including automatic completion at the final combat Concentrate boundary. Onset and recovery affect the roll without changing item Power, accepted timing or energy. The personal IQ/Symptoms exemption above remains in force. The private `item_sight_generation: 1` input marker preserves historical commands and exact retries independently of the earlier check and targeting generations; new v1 item casts also retain their targeting baseline privately. Already committed checks remain immutable. `tests/test_magic_item_vision.py` covers these boundaries and seeded old/new reexecution on SQLite and PostgreSQL.

Campaigns fourth printing B345 prohibits a non-defense success roll below effective skill 3. Corrected item completion checks the fully modified current target before distraction or casting dice and before energy payment. For example, Power15 at range11 starts at 4, but subsequent blindness makes the unresolved target −1 and completion refuses even a potential roll of 3. Rejection preserves the exact accepted cast. Its actor or a currently seated, trusted GM can cancel that unrolled Regular/Resisted cast through its saved identity without requiring the item or renewed caster/target approval. Cancellation draws no dice, pays no new energy and preserves spent time and the accepted commitment; it cannot become a new cast or retarget it. Already active spells retain the existing paid cancellation rules. These additions use the same private item-sight generation, including automatic completion, while older recorded generations retain their original checks and exact receipts. `tests/test_magic_item_vision_admission.py` covers the minimum, cancellation, lost approvals, current authority and replay boundaries on both stores.

Area spells remain a concrete #763/#785 residual: B239 uses the nearest affected edge and allows touching any part of the area, while the existing adapter binds its center and distance to a target participant and has no current area sight/touch authority. Remembering that participant does not establish those area facts. The compatibility test preserves historical Area behavior and does not certify it. Missile creation and release retain their separate spell and ranged-attack procedures. This item sight repair does not complete multiple Symptoms construction, the remaining variants, or Staff/Power acceptance under those existing issues.

## Acute blindness in combat

Characters third printing B124 and Campaigns fourth printing B394 distinguish sudden blindness from an accustomed blind fighter. An active blindness Symptom now changes actual weapon, missile and unarmed combat eligibility. Ordinary located attacks use -10; the narrower -4 case requires separately justified certainty of the target's location. Attacks against an unseen person use a random hit location. Named locations, armor chinks and deliberately selected objects cannot bypass that requirement. Ordinary attack commands with no location selected resolve as random while blind. If blindness begins after a legal body-location or armor-chink declaration but before its attack roll, current nonvisual proof is still required and the unresolved aim becomes random without a chink benefit. A committed attack roll retains its historical location and consequences.

Map coordinates and remembered visual knowledge do not establish a current nonvisual location. A private engine service persists a directed observation for one observer, target and encounter, with an actual nonvisual basis and explanation. Attack awareness and location certainty are separate facts. A source-bound Hearing attempt rolls projected Per with the purchased Acute Hearing modifier (B35), current Per condition modifiers and B394's -2. Deafness (B129) and active head-trauma deafening (B556/B422) prevent the roll; Hard of Hearing (B138) adds -4. Both supported Acute Hearing identities contribute their approved levels without counting the same trait twice. A successful roll locates the target without automatically granting exact certainty; a failed roll does not erase independently established attack awareness. Environmental acoustics and Stealth contests remain outside that bounded roll.

A blind defender aware of an incoming attack can Dodge at -4. Parry and Block additionally require a current location. An unaware defender has no active defense. The engine refreshes these permissions before defensive exertion, acrobatics, retreat or defense dice, including when Symptoms begin or clear between attack declaration and resolution. A valid retreat is part of that admitted atomic defense; it invalidates evidence for later actions. Canonical defense scores already include the -4, so their callers apply it once. Blindness replaces the visual attack contribution rather than stacking darkness and eye penalties; optical scope and laser sight bonuses are unavailable. B364 still permits Aim at an otherwise detected target. Evaluate requires sight, and a Feint requires the opponent to observe the attacker.

Sensory evidence is private, bound to the current approved actors and spatial context, and invalidated by relevant movement, form/build or blindness changes, revoked judgments and encounter changes. Current GM membership and trusted authority are checked through the ordinary transaction pipeline. Seeded reexecution, exact committed retries and compare-and-swap revisions remain authoritative. The public tactical command and event vocabularies are unchanged.

The private random-unarmed adapter supports ordinary punches and kicks. Its full resolved location and location dice remain in a typed private resource record, while actual injury consumes the complete human location. Existing grasp-based controls remain tactile. Random unarmed Wait, additional-attack and step variants, new blind arm locks, nonvisual interposition, observation of future Wait triggers and unknown-location random-direction attacks remain explicit unsupported cases. Other anatomy, purchased Blindness adaptation, composed purchased-attack hosts and transport projections require their own acceptance evidence. These residuals remain visible in the #763 acceptance review; its source-ledger row is partial with the existing issue as completion owner. Issue acceptance and whole-family readiness are separate decisions. Passing the named consumers does not certify all Symptoms constructions, all combat, or API/UI/live play.

## Continuing an interrupted declaration

Blindness can begin after an attack was declared but before it was rolled. Body aim then resolves randomly once nonvisual location is established. An explicitly selected object is not silently changed into a person target. A private, attacker-authorized `AbandonPendingAttack` command binds the exact pending ID and lets the player forgo the unresolved remainder of that maneuver. It preserves spent FP, ammunition, movement, previous strikes and the maneuver's defensive restrictions. Canonical end-of-turn injury and clock settlement run once; the command does not start a new Do Nothing turn or refund costs. The original intent and spent maneuver remain in private history.

A committed attack roll cannot be abandoned. A failed protector's interposition therefore returns to the original target's ordinary defense continuation, retaining the recorded attack. Suppression rounds are paid at zone creation; their later automatic attacks remain live after the shooter loses sight and must resolve through the pending defender, without another ammunition debit. New blind suppression declarations remain unsupported. Abandoning a waiter's unrolled reaction restores the existing interrupted-movement continuation, preserving any mandatory high-speed distance. These cases distinguish a voluntary unrolled action from consequences already incurred; none permits rollback or silent retargeting.

## Multiple Symptoms on one purchased attack

Characters third printing B109 explicitly permits several Symptoms on one Innate
Attack. The approved construction now accepts distinct effect/threshold pairs,
including pairs that reuse the same author-facing modifier option. Each pair has
its own B35–36 cost and threshold multiplier. For example, one die of burning
with coughing above one-third HP (+60%) and blindness above two-thirds HP (+50%)
costs 11 points after rounding. Exact duplicates, including equivalent numeric
attribute levels spelled differently, remain invalid; unrelated duplicate
modifiers remain invalid. Each generated cost approval binds the complete authored
parameters so one effect cannot borrow another effect's price.

The first typed effect retains its original `symptom_spec` field and legacy
source-derived ID. Further typed effects are retained in `additional_symptoms`,
which is omitted when empty. Cyclic occurrences and contagious source snapshots
with multiple effects use explicit private runtime subclasses, including the
zero-initial-damage variants. Their complete fields are validated and serialized
through the runtime resource unions. The public `CyclicAttack`/`CyclicExposure`
authoring models and all frozen schema snapshots remain unchanged; portable
scenario input rejects the private extension. Each additional effect has a distinct
stable ID, but all effects from the same attack source read the same causal injury
debt.
One hit or Cyclic occurrence records damage once, regardless of its number of
Symptoms. Reapproving the same semantic effect set in a different authored order
retains the existing effects, IDs and injury debts when its source is rebound;
changing an effect, level or threshold remains invalid for that ongoing source.
Direct composed delivery, purchased combat declarations, Cyclic ticks,
contagion snapshots and critical self-hit scheduling retain the complete effect
set. Different channels, attackers and target pools remain independent. Recovery
refreshes each effect separately and projects against the actor's current build;
it never restores an old actor or purchase snapshot.

`tests/test_multiple_symptoms.py` independently checks the printed thresholds on
12 HP/FP: injury 4 does not start one-third Symptoms, 5 does; injury 8 does not
start two-thirds Symptoms, 9 does. Healing back to 8 or 4 retains a corresponding
active effect, while healing to 7 or 3 removes it. The tests inspect actual DX/IQ
modifiers, sight and Stealth eligibility, the strongest concurrent attribute
penalty, current-build Will, resource balances and distinct causal sources. FP
recovery uses actual ten-minute rest accrual (Campaigns fourth B427); stopping a
Cyclic attack alone does not remove unhealed Symptoms.

`tests/test_multiple_symptoms_host.py` starts with a real approved purchase and
GM-bound combat source, resolves the attack and a Cyclic tick, stops that source,
and performs five daily natural-recovery checks (B424). It verifies current
consequences, unchanged approved actors, exact retries after restart, authority,
changed-intent conflicts, stale revisions, event folding and seed-only command
reexecution. Existing single-Symptom records and histories keep their prior
serialized form and roll chronology; no command generation or version increment
is needed because multiple Symptoms previously failed approved construction.

### #763 acceptance and remaining ownership

- Thresholds, source-specific recovery, repeated hits, independent attack sources
  and actual supported consequences have executable coverage for the named
  blindness, coughing and ST/DX/IQ/HT variants, including multiple effects
- Independent source expectations and real purchased-host state are tested;
  passing these tests does not establish untested gameplay consumers
- The existing transaction authority, exact retries, stale rejection and seeded
  replay remain in force; the new persisted cases exercise those boundaries
- Source/evidence status remains partial. Open #763 remains the completion and
  consequence owner; prior closed implementation issues do not own its residuals

Advantage grants, Disadvantages other than blindness, and Negated Advantage
suppression still lack a general source-bound temporary-trait consumer. Their
bounded dependency is an overlay that adds or suppresses the specified trait and
level on the *current* approved build, supplies the actual trait consumers, and
removes only the ended source without undoing later purchases or other effects.
Instantaneous Advantages also need their own once-only activation semantics.
The remaining B36 Irritants (Tipsy, Drunk, Drowsy, Moderate/Severe/Terrible Pain,
Euphoria and Nauseated) need source-derived timing, checks and eligibility wired
to this cumulative lifetime; accepting their names alone would not satisfy the
first two issue criteria. All remain explicitly rejected in approved construction
and owned by #763. The previously documented blind-combat and Area-spell residuals
also remain; Area-spell consumer work is shared with open #785. This change does
not justify closing #763 or promoting its family to fully supported/certified.

`tests/test_multiple_symptoms_cyclic_runtime.py` verifies zero initial damage and
DR-absorbed delivery through actual repeat injury, positive and zero-damage
contagious source snapshots through secondary infection, current consequences,
restart/retry/replay, and byte-stable single-effect attack/exposure records. The
catalog, live-social and scenario-document test families and the authoring,
scenario and social contract scripts must all pass against their frozen snapshots;
this runtime extension does not authorize regenerating those contracts.
