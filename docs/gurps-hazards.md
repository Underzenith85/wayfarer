# Physical feats and environmental hazards (#110)

Coverage remains **partial**, selected only for the exact Basic Set profile.
The campaign selector, saved package pins and frozen player API are unchanged.
Numeric fixtures use Campaigns fourth printing B349-354 and B430-439; they do
not certify the separately declared first-printing/errata baseline.

`PhysicalService` binds an authored route to its scene and procedure. It derives
attributes and load from the approved character and authoritative inventory,
checks injury and fatigue eligibility, and commits checks, elapsed time, FP and
injury together through the existing play store. Reconnects return the original
result. The supported procedures are short ordinary climbs, prepared/unprepared
jumps, ordinary lifts, hourly hiking exertion, short intentional swimming and
unarmored Earth-gravity falls. Results record physical capacity and elapsed time;
cross-scene destination mutation is explicitly rejected. A failed swimming entry
creates a durable drowning schedule instead of forgetting the inhaled water.

`HazardService` accepts exposure IDs, never player-authored damage or timing.
Its trusted resolver binds ambient conditions and protection. Each exposure
persists its profile, actor, HT, Will, schedule and remaining cycles in the
existing resource checkpoint. The global resource clock may reach a deadline
but cannot pass it until the exposure is settled, including advances requested
by another subgroup. Ordinary actions and new medical tasks also reject an
unresolved due exposure. CAS and command receipts prevent repeat damage or rolls.

Implemented hazard variants:

| Variant | Procedure |
| --- | --- |
| Ambient cold | Authored 10/15/30-minute HT checks and one FP on failure |
| Ambient heat | Half-hour HT checks; failure costs one FP, critical failure 1d FP |
| Fire | Authored burning dice and scenario-bound DR |
| No air | One FP per second, Will at nonpositive FP, four-minute death deadline |
| Drowning | Five-second struggle checks, then one-minute/five-minute recovery checks |
| Simple poison/disease | Authored incubation/intervals, HT resistance, toxic injury and recovery-success count |

Heat/cold and disease damage retains per-condition HP/FP recovery debt.
Ordinary rest cannot restore restricted FP, and the selected natural/physician
healing procedures cannot restore blocked HP. A trusted safe environment ends
ambient restrictions; configured successful illness recovery clears its debt.
No arbitrary player-authored cure command exists.

Remaining blockers are tracked in [#154](https://github.com/Underzenith85/wayfarer/issues/154): complete scene travel/progress integration; long climb and
daily/group hiking planning; full swimming fatigue and rescue treatment;
armor/blunt trauma and nonhuman falling; temperature-tolerance and Survival
variants; affliction-specific toxins, transmission, diagnosis and drug catalogs.
Disabled limbs and blindness from #107 explicitly reject feats that require
unsupported adaptations. Advanced treatment is coordinated with #148.
These are not claimed as verified rules, and generic authored hazard values do
not constitute an exhaustive poison/disease catalog. LLM and player-facing
dispatch remains gated by #122.

`tests/test_gurps_hazards.py` contains independent numeric fixtures, all six
physical service procedures, environmental damage, drowning transitions,
suffocation death, subgroup clock barriers, SQLite concurrent retries and replay.
