# Active defense options (#878, #835)

Characters third printing / Campaigns fourth printing. B375 Acrobatic Dodge: purchased Acrobatics required, once per turn, recorded success +2 / failure -2 to that Dodge, permitted with retreat. The command's skill roll runs only in settlement; eligibility previews consume no randomness. The turn marker resets on the actor's next turn, and the defense history retains its trace.

B377 ground Dodge and Drop: +3 against a ranged attack, then persistent prone posture after resolution. The selected defense uses its original posture; the new prone penalty applies to subsequent defenses. A ground drop cannot be combined with retreat. Aerial concealment-step cases reject pending explicit authoring rather than incorrectly making a flyer prone.

Internal ChooseDefense and additive tactical v2 accept the two options; frozen tactical v1 remains unchanged. Both option fields omit their default false value from persisted historical command payloads. Source statuses and broader certification gates remain unpromoted.

Executable evidence: tests/test_dodge_options.py checks success/failure target values, skill eligibility, no preview dice, once-per-turn validation, recorded history, actual service settlement, persistent prone posture, no bonus leakage, exact retries and rejected melee drops without mutation. Existing defense/melee/ranged/maneuver and frozen-contract suites remain regression gates.

B375 Sacrificial Dodge accepts an owned protecting actor within a step of the friend against an observable ordinary single weapon attack. Successful Dodge dispatches actual injury to the protector; failed Dodge preserves the original attack roll and leaves the friend their normal defense. Critical attacks hit the friend without an interception roll. The protecting actor spends their retreat opportunity; the Drop combination applies its ranged bonus and subsequent prone posture. Melee, thrown weapons and ordinary ammunition expenditure use existing reducers and durable service transactions. test_sacrificial_dodge.py exercises these outcomes, principal authority, rejected retreat combinations and exact retries. Specialized attacks (bursts, areas, spells, targeted objects and projectile follow-ups) still reject pending explicit consumers. Explosion/dive-for-cover and flying/swimming variants are distinct consumers and are not certified by ground ranged-drop evidence.

B375 airborne Acrobatic Dodge substitutes purchased Aerobatics for Acrobatics, with the same recorded success/failure and once-per-turn limit. B377 explosion diving tests exercise success applying declared destination cover before damage and failure applying movement after damage, persisted posture, exact retries and replay. Flying actors and actors in the explicitly authored water blast environment retain their original posture rather than becoming prone. The +3 Drop bonus remains for later defenses against the same foe until the defender’s next turn, with ordinary prone penalties still applied. Ranged aerial/swimming concealment steps still require dedicated authored geometry consumers.

B377/B415 sacrificial contact explosion responses roll one server-owned Dodge with the Drop +3 bonus. Success applies maximum contact blast damage to the protector and torso DR plus HP as cover to others; failure leaves ordinary blast damage before completing the step. The GM declares the attempt and center before entropy. Ground actual injury, failed attempts, shared friend space, retries and replay are covered by test_area_attacks.py. Aerial contact intercepts reject until altitude geometry is authored.

B368/B375 interposition steps now admit a reachable path on the current map.
Hex steps use the existing movement reducer, including blocked terrain,
occupancy, step budget and authored elevation transitions. Square steps cannot
cross blocked cells or hostile intervening occupants. This retains ordinary
successful interposition injury, the friend's failed-interposition defense and
exact command retry behavior. Focused host regressions exercise blocked versus
legal two-yard paths on both map types and both persistence stores.

This bounded correction does not certify all of issue #878. Acrobatics preceding
skill-roll condition exemptions remain to be reconciled with B375/B419/B428;
concealing aerial/swimming drops and specialized interpositions retain the
consumer boundaries above.
