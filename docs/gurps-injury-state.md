# Persistent injury and death state

Issue #514 closes the required Basic Set injury-state sequence against the
selected Campaigns fourth printing. The engine stores HP, shock, stun, posture,
consciousness, mortal-wound deadlines, death, and location injuries together in
the resource checkpoint. Commands and their check traces share the receipt
ledger, so retry and replay cannot repeat a threshold check.

## Rule binding

| Rule | Source | Engine evidence |
|---|---|---|
| General injury and shock | B418-B419 | `health.injury.apply_injury`; bounded next-turn `InjuryStatus.shock` |
| Major wounds, knockdown, and stunning | B420 | typed HT checks, posture, stun, unconsciousness, and held-item drops |
| Crippling injury | B420-B422 | `LastingInjury`, location damage caps, duration checks, and shared location validators |
| Patient status | B421 | `health.injury.patient_status` derives the four bands from persisted HP |
| Temporary attribute penalties | B421 | typed shock, fright, and hazard conditions feed authoritative check modifiers; defensive checks opt out |
| Mortal wounds and death | B423 | exact threshold-crossing checks, half-hour deadline, further-threshold death, and automatic death |

The optional accumulated-wounds and last-wounds rules on B420 remain owned by
the optional-rule disposition work (#493); they are not silently enabled by the
required profile.

## Runtime invariants

- A single large wound settles every newly crossed death threshold in order.
- Separate wounds settle only thresholds crossed by that command.
- A receipt replay returns the recorded result without drawing entropy.
- Crippled locations are consumed by combat, movement, equipment, and the
  general action validator; narration cannot bypass them.
- Recovery changes HP or an injury duration without reconstructing the patient,
  preserving permanent and pending injury facts.
