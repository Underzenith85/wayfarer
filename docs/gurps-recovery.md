# GURPS fatigue and medical recovery (#109)

The implementation remains **partial**. It adds deterministic mechanics and an
internal authoritative service; it does not certify or enable a GURPS profile in
the campaign selector. Existing prototype rules and saved profile pins remain
unchanged. The coverage ledger is [GURPS conformance](gurps-conformance.md).

## Implemented behavior

- Opted-in FP pools retain signed FP down to negative maximum FP. Further fatigue
  costs at nonpositive FP also cause HP injury through the existing injury
  reducer, including damage history and threshold checks.
- Continued exertion uses compiled Will. Failure persists collapse; a critical
  failure invokes HT for heart attack. Heart attack immediately sets negative
  maximum FP and unconsciousness, with a shared-clock death deadline unless an
  authoritative future resuscitation mechanism clears the condition.
- Low-FP ST, Move, and Dodge use a separate rounding-up helper. Base HP and
  ST-derived damage are not reduced by that helper. Battle, hiking, and heavy
  exertion costs are explicit server-side calculations, not inferred from prose.
- Quiet rest restores one ordinary FP per 600 seconds. Starvation, dehydration,
  and sleep deficits remain separate and require their own recovery conditions.
  Interrupted rest retains only completed recovery intervals before interruption.
- Natural healing requires 86,400 seconds of rest and adequate food, then an HT
  roll. A present, capable physician with skill 12+ supplies the defined bonus.
- Bandaging and first aid use recorded injury event IDs. A completed attempt
  cannot be repeated by changing the command ID or healer. Bandaging is included
  in the first-aid healing allowance, never counted twice. Technology determines
  treatment time and healing; critical failures produce new recorded injury.
- Physician treatment uses technology-specific intervals and patient limits.
  Only one physician treatment can occupy a patient's treatment interval.
  High-HP healing scales by full tens of maximum HP, starting at 20 HP.

`MedicalService` derives HT and medical skill from approved builds. Its bound
environment resolver supplies location-relevant supplies and technology; these
are not player command fields. A task stores its starting conditions and does
not advance the world clock. Existing wait and shared-party time services own
elapsed time. CAS and command receipts commit recovery once across reconnects
and restarts, together with the unchanged injury history.

Rest accrues deterministically as the authoritative clock advances, including
partial intervals before interruption. Per-cause entitlement snapshots and
consumed-credit counters prevent old rest from paying for future fatigue.
Finishing rest reports its accumulated recovery without applying it again.
Medical work stores the starting HT, skill, and HP deficit. The clock can reach
a pending recovery deadline but cannot pass it until the task is explicitly
finished; further activity involving its actor or patient is likewise blocked.
This settlement boundary preserves injury/fatigue ordering and does not revoke
an earned treatment merely because the provider's state changes afterward.
Concurrent treatment attempts sharing a patient are conservatively rejected;
one physician may still maintain bounded tasks for different patients.

Ordinary action resolution, positive injury, and fatigue costs interrupt pending
tasks. Combat adapters must call the same `interrupt_tasks` helper for maneuvers
and active defenses. Timed recovery does not implicitly occur on scene changes.
Prototype recovery shortcuts reject profile HP/FP pools instead of applying
unverified healing amounts.

## Evidence and boundaries

Numeric expectations were checked against Basic Set Campaigns, fourth printing,
B424-427 and B429. This is not certification against the repository's separately
declared first-printing plus errata baseline. Tests contain independent numeric
expectations rather than copied explanatory text.

`tests/test_gurps_recovery.py` covers FP boundaries, restricted fatigue, exhaustion,
first-aid tables, repeated treatment, injury interruption, and natural recovery.
`tests/test_medical_service.py` covers durable SQLite CAS/replay, reconnect retries,
shared-clock waiting, movement interruption, authorization, and heart-attack
deadline execution. Existing prototype tests remain regression evidence.

Advanced resuscitation and stabilization are described in
[advanced treatment](gurps-advanced-treatment.md). Still unsupported: illness-
specific healing restrictions, and trait-specific recovery rates. First-aid and
Physician commands require an available approved skill definition; the separate
catalog coverage gates are not bypassed. Exact Lite-specific source certification,
complete combat exertion dispatch, and player-facing GURPS recovery controls are
not claimed by this PR. No coverage row is marked verified by these additions.
