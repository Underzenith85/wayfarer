# Object combat integration

This completes the engine scope of #289 and #290 on the existing combat
transaction, resource receipts, and shared clock. Source certification remains
independent; completing these mechanics does not by itself close #191.

## Executable paths

- Physical projectiles and released Fireballs accept an equipped object target.
  B400 weapon size replaces the owner's size modifier. Each rapid-fire impact
  applies B483 object injury separately; the owner's HP does not receive an
  object-target wound. Object destruction ends further object HT rolls in that
  burst without converting remaining rounds into attacks on the owner.
- B401 weapon defenses exclude Block and shield DB, and Parry uses the targeted
  weapon. Unheld ground targets have no active defense. Held weapon occupancy
  includes forward hexes according to reach; ground targets use their recorded
  location and combat sight/reach validation.
- B373/B484 shield interception identifies the projectiles stopped specifically
  by DB, including when Dodge still succeeds without DB but avoids fewer rounds.
  B408 cover DR applies independently to those impacts. A shield destroyed at
  -5 times maximum HP remains carried; reaching -10 times maximum HP detaches it.
  Ordinary destroyed objects still reject further damage commands. Shield impacts
  retain full pre-DR crushing damage for mapped knockback, and every penetrating
  projectile independently rolls B484's 1-2 shield-arm routing.
- Shields contributing DB, worn protection, and an actually attempted second
  defense receive B483 stress checks. Existing per-second receipts prevent
  repeated HT rolls at the shared clock boundary. Tactical weapon choices use
  supported residual modes.
- `retrieve_equipment` starts, finishes, or cancels field recovery after the
  original encounter is completed. It requires ownership and the original
  world location. The deadline covers a round trip at compiled Move plus pickup;
  Wait and the existing shared-clock barrier supply the time. Negative/off-board
  landings remain recoverable. Finishing clears ground custody but does not
  equip or ready the item. While pending, other activities require cancellation
  or completion.
- `continue_critical_miss` requires GM authority and two explicit stages:
  `migrate`, then `resume`. The v1 continuation adapter accepts complete human
  self-wound/shoulder contexts (B556 rows 5, 6, 15), a unique original weapon mode,
  complete original grips and limb DR, and unchanged approved build/catalog.
  Original table dice are never rolled again. An original incoming parry wound
  uses its captured damage/protection independently of the self-wound. Incomplete
  or ambiguous contexts and unsupported rerolls remain blocked. Complete row-14
  contexts continue through the persisted grid/hex flight and collision path.
- A weapon used on its immediately preceding attack exposes its recent reach for
  a B400-401 counterstrike. Ground Fireball targets use their own mapped position,
  receive no owner defense, and remain targetable when their owner is occluded.

## Special objects and salvage

- Sentient machine inventory bodies bind one actor injury pool. Object attacks
  use that actor's shock, stunning, unconsciousness, hit-location and death state;
  the item never stores a competing HP condition.
- Pinned Brittle, Combustible, Flammable and Explosive profiles execute B136 and
  B483-484 thresholds. Ignition, per-second 1d-1 burning damage, HT dice, explosive
  failures and blast payloads are retained. Explosions arm the existing same-clock
  blast resolver for authoritative secondary damage.
- B485 outcomes can name both the retained weapon mode and a detached catalog item.
  Detached pieces receive stable IDs, exact catalog mass, original ownership and
  the break location; their catalog modes carry skills, reach, damage and penalties.
- Below one-third HP, reduced effectiveness changes only through an explicit
  engine-authority selection from pinned definitions; no universal penalty is invented.
- Damaged and destroyed artifacts may expose a pinned timed dismantling recipe.
  It records the original actor, item and tools, rolls once after the shared-clock
  deadline, consumes the source, and creates recovered material under the same owner.

## Tactical v2

The browser uses `/api/tactical/v2`. Its equipment view exposes only the
controlled character's condition, ground location, pending repair/retrieval
work, remaining seconds, and previewed work commands. Previews consume no dice.
The frozen v1 response has no equipment extension and omits object-target choices
that its command contract cannot accept. Damage amounts remain engine authority.

## Acceptance boundary

The #289/#290 engine work is complete. Browser controls beyond the existing
tactical-v2 choices remain in the separate API/UI milestone and do not block the
engine-only Basic Set milestone.

Numeric and persistence evidence lives in `test_projectile_objects.py`,
`test_equipment_retrieval.py`, and `test_critical_continuation.py`. Real
multi-identity HTTP coverage is in `test_tactical.py`; browser component coverage
is in `frontend/src/play/tactical.test.tsx`.
