# Active defense options (#878, #835)

Characters third printing / Campaigns fourth printing. B375 Acrobatic Dodge: purchased Acrobatics required, once per turn, recorded success +2 / failure -2 to that Dodge, permitted with retreat. The command's skill roll runs only in settlement; eligibility previews consume no randomness. The turn marker resets on the actor's next turn, and the defense history retains its trace.

B377 ground Dodge and Drop: +3 against a ranged attack, then persistent prone posture after resolution. The selected defense uses its original posture; the new prone penalty applies to subsequent defenses. A ground drop cannot be combined with retreat. Aerial concealment-step cases reject pending explicit authoring rather than incorrectly making a flyer prone.

Internal ChooseDefense and additive tactical v2 accept the two options; frozen tactical v1 remains unchanged. Both option fields omit their default false value from persisted historical command payloads. Source statuses and broader certification gates remain unpromoted.

Executable evidence: tests/test_dodge_options.py checks success/failure target values, skill eligibility, no preview dice, once-per-turn validation, recorded history, actual service settlement, persistent prone posture, no bonus leakage, exact retries and rejected melee drops without mutation. Existing defense/melee/ranged/maneuver and frozen-contract suites remain regression gates.

Sacrificial Dodge and its Drop combination remain unverified under #878 until interposition, preserved attack-roll timing, protecting-character damage, failed interposition leaving the friend a normal defense, movement and authority are implemented and exercised. Explosion/dive-for-cover and flying/swimming variants are distinct consumers and are not certified by ground ranged-drop evidence.
