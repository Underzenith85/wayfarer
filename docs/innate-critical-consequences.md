# Private Innate Attack critical consequences

Implementation owner: [#764](https://github.com/Underzenith85/wayfarer/issues/764).
This family-local reducer supplies consequences to the persisted composed-attack
host. It does not add a public command, equipment item, ammunition requirement,
or an unarmed classification for natural ranged attacks.

Source selection is Characters third printing B61–62/B201 and Campaigns fourth
printing B381–382/B556–557. `tests/test_innate_criticals.py` contains independent
resulting-state expectations. Those tests distinguish printed consequences from
explicit campaign adjudications; they do not certify every Innate Attack variant.

## Automatic consequences

- Ranged failure by ten below 17 is an ordinary miss. Defense retains the ordinary
  failure-by-ten rule. A critical Dodge failure makes the target prone, including
  its canonical injury state, and does not draw an attacker critical-miss table
- The ordinary torso hit table supplies basic-damage multipliers, maximum damage,
  post-divisor halved DR rounded down, forced major-wound checks and double shock
- Row 12 drops actual held objects even with zero basic damage or penetration.
  It clears readiness and grips and writes actual mapped or world-ground custody;
  worn armor is preserved
- Every ranged source rerolls an initial general miss 5/6 exactly once. A final
  5/6 can apply canonical self-injury using captured purchased dice, limb DR,
  armor divisor, physiology, vulnerability and survival traits. The engine draws
  body/side and damage dice, then applies full or half basic damage before DR
- General miss 7/13/16 causes the same balance restriction: no actions, including
  free actions, and -2 active defenses until the subject's next turn. Row16 does
  not make a ranged attacker fall. The very first next-turn maneuver is legal
- A described real emitter arm supports the literal 1,800-second arm disable.
  This inflicts no HP damage and does not compel the attacker to drop objects
- Real, independently bound held sources support break, drop and extra Ready
  effects. Actual resistant quality captures its one confirmation roll. Energy
  type or Innate Attack specialty never implies that quality
- Self-hits use actual initial resistance and register Cyclic occurrence/recovery
  debt and Symptoms against their actual HP/FP cause. The outcome identifies the
  new occurrence and hit limb for the host's current-context due binding. A zero
  initial half-damage result still schedules its purchased future damage dice;
  successful resistance creates no occurrence or debt
- Ordinary toxic and fatigue self-hits do not affect machines (B61). This applies
  before resistance, location or damage dice and creates no Symptoms or Cyclic
  cause. Special toxic attacks that override this immunity require their own
  approved runtime consumer

The host calls `body_hit_effects`, `critical_dodge_failure`, `drop_all_held`,
`resolve_innate_miss` and, where necessary, `continue_innate_miss`. It must call
`require_innate_action` in ordinary and free-action admission, preserve active
defenses, and supply the stable source ID for source-use restrictions. The helper
uses captured encounter turn identity and canonical injury turns so admission
does not require an extra Do Nothing turn. Existing combat next-turn processing
resets the actual defense penalty.

`require_innate_actor_action` supplies the same guard to noncombat hosts. After an
encounter ends, same-second actions remain prohibited; an elapsed second supplies
the next-turn boundary (Campaigns fourth B363). Time-only Wait/Do Nothing remains
available, so the ended encounter cannot leave the actor permanently blocked.

## Fatigue interpretation

Fatigue damage remains on the FP ledger; machines are immune. Maximum/multiplier
and DR effects operate before that ledger. Row8 does not manufacture HP injury or
shock from FP loss. Actual HP overflow passes double shock to canonical injury.

For rows7/13/14, the implementation adopts the table's literal positive-penetration
condition. `apply_fatigue_major_wound` performs the canonical HT/knockdown effect
without creating a Wound or reducing HP. Its evidence explicitly records
`B556-literal-positive-penetration`. An overflow major-wound check is reused only
with its actual captured CheckTrace; failed checks still drop held objects. This
is an explicit cross-rule interpretation, tested separately from HP injury.

## Persistent adjudication and remaining ownership

The selected pages do not define a universal break/drop/ready/weapon-arm
substitution for inventory-free Beam, Breath, Gaze and Projectile sources. An
inapplicable result captures the original attack, complete approved profile,
source/build/description and target bindings, table/reroll chronology, subject
pools and actual objects. It retains the pending attack and a specific stage.

A current authorized GM can supply a named policy and reason selecting an actual
balance loss or a bounded timed source disable. The engine applies that effect
once. This result is marked `gm-adjudication`, retains its exact choice and
principal, and is never represented as an automatically verified table result.
The host must verify current campaign GM seating/trust, own CAS/retry authority,
finish the original spent maneuver and settle the encounter after continuation.
The reducer never rerolls, redeclares, replaces the table or accepts an injury
number. A changed digest, pending attack identity, backward chronology, or consumed
continuation identity rejects visibly. Intervening damage, fatigue, body changes
and dropped objects remain current facts: they do not strand the committed
attack or get restored from its capture. GM effects begin at the continuation's
recorded time; balance also uses the actor's current injury turn.

Still owned by #764:

- Automatic interpretations of otherwise inapplicable natural-source rows; a
  campaign-authored policy makes play resumable but is additional adjudication
- The host must bind self-hit Cyclic occurrences to their actual limb for future
  protection and wounding. A later body transformation or missing limb requires
  a source-grounded current-body policy; it cannot silently relocate damage to
  the torso. Modifier effects are not omitted from the initial self-hit
- Nonhuman self-hit topology, targeted head/limb critical-hit variants, and
  linked Alternative Attack disable semantics need their actual bounded adapters
- The persisted host must prove membership, CAS/races, restart, seed replay,
  settlement and frozen-contract compatibility on both supported databases;
  reducer tests alone do not prove those joins

Existing public vocabulary and older weapon/spell snapshots remain unchanged.
