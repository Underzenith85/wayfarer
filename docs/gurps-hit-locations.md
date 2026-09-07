# Hit locations and lasting injuries

The Basic Set combat adapter accepts an optional `hit_location` on its existing
attack command. The selected location is persisted with the defense pause;
random locations are rolled only for a hit that defeats defense. The resulting
location, dice, effective DR, HP loss and lasting injury IDs are recorded in the
combat receipt. Restarting or retrying a command cannot reroll them.

This is a partial, explicit living-human implementation. Trusted scenario actors
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
critical limb hits can apply the timed funny-bone result. Deafness and permanent
appearance aftermath remain explicit persisted blockers; they are never treated
as ordinary damage or granted invented character effects. Their cross-system
implementation is tracked in [#153](https://github.com/Underzenith85/wayfarer/issues/153),
which depends on the location, trait and social dispatch work. Nonhuman anatomy,
Injury Tolerance, optional cumulative wounds, assisted/crawling movement and
cross-system lasting-disadvantage effects remain unsupported.

## Evidence and source status

Independent cases in `tests/test_hit_locations.py` cover limb/vitals/skull/eye
injury, divisor rounding, random locations, temporary/lasting/permanent duration,
critical head arithmetic, shield impairment and SQLite replay. Runtime arithmetic
was checked against *Basic Set: Campaigns*, Fourth Edition, fourth printing,
B379, B398-400, B420-422, B552 and B556-557. These cases do not certify the frozen
first-printing plus January 26, 2007 errata baseline. The profile capability gates
and coverage matrix remain partial until that evidence and the named remaining
mechanics are complete.
