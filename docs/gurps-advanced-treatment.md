# Advanced treatment (#148, #209)

Coverage remains partial. The internal medical reducers support heart-attack
resuscitation, mortal-wound survival checks, surgical stabilization, trauma
maintenance, and surgery for lasting crippling injuries for the exact Basic Set
profile. Campaign activation and player-facing controls remain behind their
existing profile gates.

Resuscitation records a one-minute task. It uses approved Physician skill or
First Aid at -4, requires TL7+, and must finish strictly before the persisted
heart-attack deadline. Success clears that deadline, leaves exhaustion intact,
and sets HP to the lower of zero or current HP. Failed attempts consume time and
can be repeated. Interrupted work never earns a rescue roll.

New mortal wounds record their next half-hour survival deadline. The shared clock
cannot pass it until an authoritative `mortal-check` command rolls compiled HT.
Failure kills; success schedules the next check; critical success removes the
mortal condition but leaves the patient incapacitated. Death or miraculous
survival retires outstanding treatment. Existing mortal checkpoints without a
deadline require explicit migration and cannot silently advance.

Stabilization takes one hour of uninterrupted surgery. The trusted scenario must
supply a sterile surgical facility at TL6+; the caregiver needs approved Surgery
and Physician skills. Technology, anesthesia, explicit scenario surgical
modifiers, severe negative HP, and cumulative failed attempts modify the stored
roll. Survival checks take precedence even at the operation's completion time.
Success removes the mortal condition without restoring HP or consciousness.
Failure inflicts recorded 3d injury through the existing injury reducer.

## Trauma maintenance (#209)

At TL6+, an authoritative caregiver can replace the half-hour mortal-wound check
with trauma maintenance. The reducer stores an uninterrupted one-hour task and
settles against the higher of patient HT or caregiver Physician. A life-support
setting supplied by the trusted scenario changes the cadence to one day. Success
moves the next mortal-wound deadline by the same interval; failure kills; critical
success removes the mortal condition but leaves the patient incapacitated. An
interrupted task earns no roll and restores the ordinary half-hour survival
cadence from the interruption point. The cadence and caregiver skill are captured
at start so reconnect/retry cannot substitute a better treatment context.

## Surgery for lasting crippling injuries (#209)

An active lasting crippling injury may be selected by its durable injury ID for a
two-hour Surgery task. Basic equipment modifiers follow TL, scenario-owned
quality modifies them further, an unclean/unsterile operating area applies its
Surgery penalty, and missing anesthetic at TL5+ applies the documented additional
penalty. Success converts the remaining recovery clock from months to weeks while
preserving the injury record. A failed operation inflicts 3d injury through the
existing injury reducer; a critical failure additionally makes the crippling
injury permanent. Interrupted surgery rolls neither Surgery nor infection and
leaves the lasting injury unchanged.

For surgery before TL5, and optionally at TL5 when the trusted scenario declares
that antiseptic practice is inadequate, settlement performs the postoperative
infection resistance check. A failed check creates an ordinary `disease`
`HazardSchedule` rather than a second disease engine. Its daily failures inflict
1 HP through the existing hazard/injury path, and a successful disease check ends
the schedule. Scenario-owned infection modifiers are captured with the task.

Permanent crippling repair deliberately fails closed. The Basic Set makes the
availability, required prosthetic/transplant resources, and exact procedure a
setting/GM decision; Wayfarer therefore does not synthesize a generic operation
from the broad TL and skill guidance. An authored procedure must be introduced
explicitly before such an action can become legal.

Task conditions are captured on start, and every settlement uses existing
receipts, injury history, recovery task state, shared time and hazard scheduling.
No command carries player-selected skill, equipment quality, technology, or free
healing. Numeric checks use B223, B423-424 and B444; this does not by itself
certify the frozen Basic Set profile.

`tests/test_advanced_medical.py` covers the original deadline ordering, failed
stabilization and SQLite reconnect/replay cases. `tests/test_recovery_variants.py`
adds independent trauma-maintenance cadence and target boundaries, interruption,
lasting-injury recovery conversion, surgical failure, infection scheduling,
replay after JSON reload, and explicit permanent-repair rejection.

Still gated: setting-specific permanent-injury repair procedures, resurrection,
drowning-specific resuscitation, trait/supernatural recovery effects, additional
disease/environment variants, and the player-facing recovery workflow. Those
remain with their dedicated follow-ups rather than being inferred from narration.
