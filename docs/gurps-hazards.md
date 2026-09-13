# Physical travel and environmental hazards (#110, #154, #517)

These services select the exact Basic Set profile. Numeric evidence uses the
supplied Campaigns fourth printing B349-355/B430-443 and Characters third
printing B93/B223-224. First-printing/errata certification remains separate;
this change does not enable the frozen player/LLM rules package.

## Travel

`PhysicalService` resolves trusted `PhysicalRoute` IDs against the actor's current
location, approved build and inventory. Its durable results retain route geometry,
progress and elapsed time. Changing geometry during a journey is rejected; new
journeys need new authored IDs. Command retries return the original result.

- Long climbs check at entry and at each five-minute boundary. A failed check
  falls from the reached height, with an authored safety-rope limit except on
  critical failure. Failed climbs restart at zero progress.
- Hiking rolls once per actor/day, shared across route IDs. An hourly segment
  charges exertion and uses the route's authored walking-day allocation for
  progress; 86,400-second segments use B351's full-day mileage with built-in
  preparation/rest. Full-day travel is not a separate healing award.
- Group hiking derives every member's load, injury/fatigue Move and skill.
  Leadership 12+ permits a roll against average Hiking; otherwise members roll
  separately. Existing daily rolls are retained on regrouping. The slowest pace
  governs the group. Exhausted/incapacitated members must be stabilized first.
- Swimming preserves elapsed activity across short commands: ordinary swimming
  checks recur at five minutes; top-speed fatigue checks recur each minute,
  using the better of HT or Swimming. Slow swimming checks fatigue each half
  hour. Injury and fatigue reduce water Move. Failed entry records a drowning
  schedule, whose recovery checks must be settled before further travel.
- Falls use HP, gravity, air pressure and the body's authored terminal velocity.
  Acrobatics can reduce a controlled fall by five yards. Worn torso armor counts
  as flexible for blunt trauma; innate DR does not. These are general-impact
  falls, not the optional random hit-location variant.

A route can bind `destination_id` and `exit_id` to an authored scene exit.
Completion uses `SceneService` for exits, entry discoveries, scene cursors and
journals without charging travel time twice. Groups arrive together. Incomplete
travel leaves actors in the origin scene. Shared-clock hazard and recovery
barriers still reject advances beyond unresolved deadlines. Split-party activity
must still use the existing scheduler; immediate physical commands cannot bypass
that barrier.

## Exposure and treatment

`HazardService` binds exposure and protection from trusted scenario context.
Optional measured-temperature context derives intervals/modifiers from wind,
clothing and wetness, approved Temperature Tolerance, and HT-based Survival.
The temperature extension allocation and racial comfort-zone center are trusted
character/environment bindings, not player-supplied bonuses. Land Survival
specialties can default to Arctic/Desert at -3 (B224).

`poison_spec` provides literal B439 profiles for arsenic, cobra venom, cyanide,
mustard gas, nerve gas with paralysis, smoke and both tear-gas delivery effects.
Cobra venom records cumulative injury for its DX thresholds. Coughing modifies
checks and prevents Stealth; paralysis prevents voluntary physical actions;
toxin blindness prevents vision checks. Retching/seizure configurations use the
existing timed-condition runtime. Nerve-agent affliction duration must be
explicitly authored because B439 does not supply one. Gas exposure duration is
part of the trusted delivery binding; the catalog does not establish exposure or
penetration by itself.

Disease contact context resolves a transmission roll before incubation and
symptomatic damage cycles. It uses the least favorable B443 contact modifier,
not their sum. Initial rolls of 3-4 retain natural immunity for the same named
variant. Contact modifiers do not leak into recovery rolls. Illness recovery
restrictions retain injury debt until recovery or the final cycle.

`HazardCareService` supplies scenario-bound, authenticated care:

- Lifesaving uses Swimming-5 plus the rescuer/victim ST difference. Failure costs
  1 FP and enforces a minute before retry; critical failure costs 6 FP and records
  that this rescuer must abandon the attempt. Success requires a safe landing
  and retains the condition for resuscitation.
- `MedicalService` performs one-minute resuscitation tasks for rescued drowning
  victims, respecting fatal deadlines. Learned First Aid at TL7+ uses -2 for
  drowning. Success ends the drowning schedule without erasing prior wounds.
- Diagnosis requires symptoms and preserves the check/identified condition.
  Reissuing an examination cannot reroll the same cycle's symptoms.
- Antibiotics require a diagnosis, TL6+ and an owned scenario-bound dose. One
  dose is consumed atomically. Most bacterial diseases receive +3 to cyclic
  recovery; viral and drug-resistant conditions do not. Bonuses do not stack.

The arbitrary pharmacology/addiction rules and optional falling hit locations
are not certified by these variants. Nor do the services silently author
racial adaptations, safety equipment, exposure vectors or remedies. Such context
must be bound by the scenario. Heat/cold/disease debts and existing suffocation,
fire, mortality and subgroup deadlines continue to use the shared resource
checkpoint and receipt ledger.

## Audited environmental families

Issue #517 reconciles the earlier #154 schedules against Campaigns fourth
printing B428-B437. `HazardEnvironment` records the measured medium, intensity,
duration, source class, pressure and temperature where applicable;
`HazardProtection` independently records seals, breathing and eye protection,
insulation, pressure/vacuum support, radiation PF and nonmetallic electrical DR.
The absence of one of those facts never means that an atmosphere is breathable
or that a character has working protection. Extended variants reject unless both
records are present.

The pinned constructors and their reducer routes are:

| Family | Authored variants and reducer | Source |
| --- | --- | --- |
| Acid | splash and immersion corrosion; swallowed acid rolls its total injury once, then schedules one-point delayed ticks; all HP uses `apply_injury` | B428 |
| Atmosphere | trace corrosive, pollutant, lethal and dense toxic gas, plus unbreathable composition; seals and air supplies are separate facts | B429 |
| Pressure | measured native-pressure crushing by failure margin and rapid-decompression illness; Pressure Support is explicit | B429, B435 |
| Cold and heat | the reconciled ambient procedure, icy-water thermal shock, and intense-heat delay based on authored DR; FP uses `apply_fatigue` | B430, B434 |
| Electricity | nonlethal, lethal and localized current with strength and insulation; stun, unconsciousness and cardiac consequences enter existing health state | B432-433 |
| Fire and objects | the existing actor fire schedule plus material-class ignition from the same burning damage command and object receipt | B433 |
| Acceleration | measured home-gravity ratio and posture; failure-margin FP and critical blackout use existing reducers | B434 |
| Radiation | dated per-source effective dose after PF, the full dose/outcome table, and thirty-day delayed decay to the retained fraction | B435-436 |
| Seasickness | one first-day shipboard HT+5 check and the existing nausea/retching condition runtime | B436 |
| Suffocation and vacuum | the reconciled one-second FP and four-minute fatal deadline; vacuum protection and blood-oxygen delay are explicit, and explosive decompression adds its immediate injury | B436-437 |

Leaving an exposure retires only future ticks and cannot skip a tick already due.
Changing intensity or protection requires a new trusted binding; an existing
schedule is immutable and command replay returns its original receipt. Hazard
results intentionally omit source class and scene. Folded `hazard.resolved`
events are visible only to the affected actor and the GM, so a hidden source or
location is not disclosed to other members.

`CombustionFacts` covers the five flammable material classes and nonflammable
materials. Tight-beam thresholds are scaled before `apply_burning_object`
composes the result with `apply_object`; it never creates a parallel object-HP
ledger. Prolonged-contact ignition beyond the single-roll thresholds remains an
explicit unsupported adjacent variant. The optional random hit-location rule for
falls is also disabled; the general collision/falling procedure remains the
reconciled #154 implementation.

`tests/test_hazard_variants.py` includes independent numeric expectations,
compiled trait/Survival use, real SQLite travel/group/rescue/diagnosis/antibiotic
transactions, inventory consumption and replay. `tests/test_gurps_hazards.py`
retains the original hazard barriers and concurrent retry evidence.
`tests/test_environmental_hazards.py` supplies protected and unprotected
boundaries for every #517 family, independently entered table outcomes, durable
radiation decay, object-reducer composition, replay and audience evidence.
