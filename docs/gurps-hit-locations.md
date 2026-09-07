# Hit locations and lasting injuries

The Basic Set combat adapter accepts an optional `hit_location` on its existing
attack command. The selected location is persisted with the defense pause;
random locations are rolled only for a hit that defeats defense. The resulting
location, dice, effective DR, HP loss and lasting injury IDs are recorded in the
combat receipt. Restarting or retrying a command cannot reroll them.

This is a partial, explicit human-layout implementation. Trusted scenario actors
declare `body: {anatomy: "human", male_groin: false}` and `held_item_hands` pairs.
An omitted body remains undeclared; unsupported anatomy is rejected instead of
becoming human. Existing campaigns are not silently migrated. Location attacks
require authoritative defender weapon/shield grips, while the Ready maneuver can
select `ready_hand` as `left-hand`, `right-hand` or `both`. Grips persist through
combat and subsequent encounters. These additive commands belong to the existing
combat adapter, not the frozen player v1 action contract.

Implemented numeric rules cover targeted penalties (including the shield side),
the human random table, rear random face-to-skull conversion, skull DR, eye/vitals
eligibility, location wound multipliers, fractional and positive armor divisors,
limb/extremity damage caps, major-wound checks, crippling and dismemberment.
Shield-hand and shield-arm injuries differ: a disabled hand can still carry a
shield; a disabled arm cannot block and loses one point of shield DB; severing
the arm drops it. Two-handed grip loss has a recorded DX retention check.

Lasting injury records live beside HP in the resource ledger. At combat end the
same authoritative transaction resolves each pending crippling duration once.
Temporary effects last until full HP, lasting effects until their recorded
deadline, and permanent loss persists despite ordinary HP recovery. One month
uses the engine's explicit 30-day interval. Timed shoulder and funny-bone effects
expire on shared game time. Weapon use, active defenses, standing and supported
walking consult these records. Unsupported assisted movement cannot bypass them
through ordinary scene travel. Lost eyes affect melee targeting and defenses;
broader sensory/trait interactions remain outside this partial capability.

Critical head blows use their own numeric table, including eye conversion,
maximum damage, DR rounding, major-wound forcing, the forced Do Nothing turn and weapon drops. Ordinary
critical limb hits can apply the timed funny-bone result. Penetrating critical-head
rows 12/13 now create typed, durable deafness for crushing damage and permanent
one-level scarring for other damage (two levels for burning or corrosion). Deafness
uses the existing crippling-duration settlement and ordinary recovery lifecycle;
scarring exposes an appearance-level loss to social consumers without rewriting an
approved character trait. Both consequences survive retry and reload. Nonhumanoid location tables,
optional cumulative wounds, assisted movement and
cross-system lasting-disadvantage effects remain unsupported.

## Evidence and source status

Independent cases in `tests/test_hit_locations.py` cover limb/vitals/skull/eye
injury, divisor rounding, random locations, temporary/lasting/permanent duration,
critical head arithmetic and aftermath, shield impairment and SQLite replay. Runtime arithmetic
was checked against *Basic Set: Campaigns*, Fourth Edition, fourth printing,
B379, B398-400, B420-422, B552 and B556-557. These cases do not certify the frozen
first-printing plus January 26, 2007 errata baseline. The profile capability gates
and coverage matrix remain partial until that evidence and the named remaining
mechanics are complete.

## Injury Tolerance and targeted near misses (#107 follow-up)

Trusted scenario `body.tolerance` facts now carry living, unliving, homogenous
or diffuse structure and No Brain/Eyes/Head/Neck/Vitals variants into the saved
HP injury status. They are anatomical runtime inputs, not player damage
modifiers or automatic purchase definitions; trait catalog/compiler ownership
remains #113/#118/#119. This does not publish a selectable profile. Omitted
metadata preserves existing human behavior and serialization.

The injury reducer applies structure-specific impaling/piercing factors,
removes applicable location multipliers/knockdown and groin shock effects,
rejects targeting absent parts, and redirects random missing locations to the
torso without extra rolls. Eyes can still be crippled with No Brain. Critical
head handling respects these variants and never invents a missing eye. Missing
both eyes uses the existing blindness combat penalties.

Diffuse single attacks cap injury at 1 for impaling/piercing and 2 otherwise.
The internal Wound source distinguishes area exposure from direct internal HP
loss: scheduled fire/falling use area injury, while fatigue spillover, medical
loss and disease/poison bypass single-attack caps. Only authoritative reducers
can supply this classification. Existing default Wound receipt hashes remain
unchanged. Tolerance and lasting impairments survive checkpoint/retry.

For the B552 note-1 locations, an ordinary miss by exactly one can hit the torso;
it still permits the selected defense. Automatic/critical failures do not gain
this fallback, and melee does not redirect into a torso made unreachable by
height. Melee and ranged receipts keep the original roll and actual location.

Independent numeric and persistence tests are in
`tests/test_geometry_injury_followups.py`, including executed entries in the
conformance ledger. The executable registry now correctly records the three
#107 families as partial, matching their existing implementation rather than
claiming absence. Full source reconciliation remains #191; #153 and the
nonhumanoid/assisted-movement boundaries above remain unfinished.
