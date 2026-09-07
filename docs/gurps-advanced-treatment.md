# Advanced treatment (#148)

Coverage remains partial. The internal medical service now supports heart-attack
resuscitation, mortal-wound survival checks, and surgical stabilization for the
exact Basic Set profile. Campaign activation and player-facing controls remain
behind their existing profile gates.

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

Task conditions are captured on start, and every settlement uses existing CAS,
receipts, injury history, and shared time. No command carries player-selected
skill, equipment quality, technology, or free healing. Numeric checks use B223,
B423-425 and B429; this is not certification of the frozen baseline.

`tests/test_advanced_medical.py` covers deadline ordering, numeric skill and HP
boundaries, interruptions, failed surgery and retries, mortality, and SQLite
reconnect/replay with authentication checks.

Still gated: trauma maintenance/life support, surgery with infection exposure,
lasting/permanent injury repair, resurrection, drowning-specific resuscitation,
trait recovery effects, and the player-facing recovery workflow. Illness
restrictions are integrated separately by #110. General surgical modifiers that
depend on anatomy or diagnosis must be supplied by the trusted scenario; this
does not infer them from narration or certify arbitrary surgery.
