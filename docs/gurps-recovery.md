# GURPS fatigue and medical recovery (#109, #515, #516)

The profile as a whole remains **partial**, while #728 verifies the three recovery
capability families against the selected Basic Set artifacts. It adds deterministic mechanics and an
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
- Injury unconsciousness uses persistent shared-clock tasks: automatic recovery
  after 15 minutes above 0 HP, hourly HT attempts above negative maximum HP,
  and a single wake attempt after 12 hours at or below negative maximum HP.
  Failure of that deep wake attempt changes subsequent 12-hour rolls to survival
  checks until successful mortal-wound treatment unlocks recovery. Outcomes retain
  their dice trace and survive JSON reload.
- Selected TL9+ healing drugs are explicit procedures. The bound care environment
  supplies an individually tracked dose, form, and authored HP/FP effect. Starting
  treatment consumes that inventory item exactly once; pills, contact agents,
  aerosols, and injections use their cited onset bands. Interruption consumes an
  administered dose without granting its full effect.

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

Numeric expectations were checked against the selected Basic Set: Campaigns
fourth printing, B423-427 and B429. The B423-B425 recovery rows owned by #515
are source-reviewed and verified; this does not certify adjacent illness,
fatigue, or optional medical variants.
Tests contain independent numeric
expectations rather than copied explanatory text.

`tests/test_gurps_recovery.py` covers FP boundaries, restricted fatigue, exhaustion,
first-aid tables, repeated treatment, injury interruption, and natural recovery.
`tests/test_medical_service.py` covers durable SQLite CAS/replay, reconnect retries,
shared-clock waiting, movement interruption, authorization, and heart-attack
deadline execution. Existing prototype tests remain regression evidence.

Advanced resuscitation and stabilization are described in
[advanced treatment](gurps-advanced-treatment.md). Still unsupported: authored
permanent-crippling surgery details and non-healing ultra-tech drug effects.
First-aid and
Physician commands require an available approved skill definition; the separate
catalog coverage gates are not bypassed. Exact Lite-specific source certification
and player-facing GURPS recovery controls are not claimed by this implementation.
Basic Set deprivation, sleep, and foraging are described in
[survival procedures](gurps-survival.md).

### One medical procedure API

`wayfarer.engine.simulation.health.medical` owns `CareContext`, `BeginRecovery`, `FinishRecovery`,
`RecoveryResult`, and the procedure dispatcher. Trauma maintenance and lasting
injury surgery use the same API as first aid and rest. The former
`recovery_variants` imports are compatibility aliases.

Recovery task schema 2 records an advanced `procedure` explicitly. Its read
migration lifts schema 1 `variant:trauma:` and `variant:repair-lasting:` markers
into that discriminator, preserving the original timing, skill, injury reference
and infection parameters. A persisted `finish-recovery-variant` command is
normalized to `finish-recovery`. Ordinary task kinds retain their saved shape.
Recorded streams still fold; reviewed fixtures verify current engine re-execution.

Authored setback recovery remains separate: it rejects profile injury/fatigue
pools and only applies scenario-defined generic pool grants. It cannot bypass
timed medical treatment or perform GURPS injury math.
