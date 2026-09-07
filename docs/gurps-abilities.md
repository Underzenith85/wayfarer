# Representative Basic Set abilities

Issue #118 adds opt-in, pinned runtime bindings, not a complete supernatural
catalog or Basic Set certification. Registered profile availability is unchanged.
The numeric references are Characters third printing B46, B48, B61, B69-70,
B106 and B111, and Campaigns fourth printing B366/B550; the historic baseline
printing delta remains an audit item.

`rules.abilities.definition` supplies construction metadata for four families:

- Burning Innate Attack with mandatory Malediction 1: a resisted Will contest,
  -1 per yard, rule of 16, burning injury through the signed HP reducer. This is
  not a conventional ranged attack and has no Dodge/Block resolution.
- Damage Resistance with Costs Fatigue: one minute of protection, added to
  equipped torso armor by the melee service. Maintenance costs half the initial
  FP, rounded up; unpaid effects expire, and the owner can cancel early.
- Rare-category Detect: authored presence/direction/quantity channels, optional
  Precise or Vague, and a separate IQ analysis action. Neither this ability nor
  the player command has general access to world facts.
- Mind Reading: approved living targets with shared language, surface-thought
  channels only, and optional Telepathic suppression. Repeated failures incur
  -2 per attempt in the last hour; a critical failure blocks that subject for
  24 hours. Digital and language-independent variants reject explicitly.

Other modifiers and unsupported combinations reject during compilation, not
just at execution. Costed activation uses the shared fatigue ledger. Actor
authority, approvals, exact profile, injury turns and exhaustion are checked by
`AbilityService`; server-owned dice and private traces share the durable store.
Successful retries return the original outcome without repeating costs or dice.

Attack, detection, reading and analysis use persisted `activate`/`analyze` then
`resolve` concentration. The shared clock must advance one second before
resolution. Other actions abandon concentration; damage and active defense
require Will-3 to preserve it. Shock applies to IQ, not Will. Changing a build
invalidates pending concentration. Encounter actions obey the current actor,
defense pause and round clock. Split groups must synchronize first.

Scenario authors supply `ScenarioContent.abilities`, which round-trips into
`ActionRules.abilities` and the configuration digest. Channels are exact actor,
target and location permissions; encounter distance comes from current
placements. This subset does not support remote channels, alternate power
systems, conventional ranged Innate Attacks, or arbitrary mental fact queries.
No profile migration or broad capability gate is bypassed.
