# Recorded medical recovery

Medical `gurps-recovery` commands replay through the actual `MedicalService`,
separately from authored `apply_setback` and `choose_recovery` commands. Public
Begin/Finish models and opaque player recovery choices are unchanged. This fixes
deterministic replay; it adds no healing or limb-restoration rules.

Fresh Begin commands capture a private, strict version-1 care snapshot containing
the complete server-resolved environment, original intent, command identity,
profile, configuration and prestate fingerprints. The source is explicitly
labelled configuration-default or trusted-scenario-snapshot. Initial capture is
provisional: the transaction revalidates the exact prestate and live trusted
environment under CAS before calling the medical reducer or rolling treatment
dice. A changed source rejects atomically. A callback identity or a digest does
not establish external world truth; this records what the trusted server source
accepted for that command.

Replay uses that original snapshot through the same service. Exact retries reuse
the accepted input and result without refreshing treatment conditions or RNG.
Player retries validate the original accepted actor, revision and deterministic
opaque choice instead of silently accepting another choice under the same ID.
Finishing reads its original task. The returned player projection may preview new
care choices from the current environment; those previews are separate from
reexecuting treatment. Private care provenance is absent from player campaign
and stream projections.

Historical Begin inputs without captured care context explicitly report that
trusted original context is unavailable. Neither current defaults, current
callbacks nor a later task/result substitute for that missing original input.
Historical Finish can reexecute when its task exists in the original prestate,
without consulting an environment callback. Its retained input bytes, including
the supported finish-kind migration, remain unchanged. An isolated historical
Finish or event fold does not prove original-genesis Begin reexecution.

The regression evidence includes actual approved-genesis Staff/Wither injury,
registered opaque bandaging, real Wait and Finish on SQLite and PostgreSQL;
full event fold and seeded command reexecution; and a genuinely purchased First
Aid/TL8 skill. With the same successful roll and healing die, default TL8 care
takes 600 seconds and heals 2 HP, while trusted TL7 care takes 1,200 seconds and
heals 1 HP. The existing permanent magical cripple remains. Separate tests cover
strict capture faults, forged caller fields, authority/stale rejection, retry and
restart, competing CAS, late rollback, privacy and unchanged authored recovery.

This addresses the replay criteria in #1020. It does not certify every medical
procedure, generic limb restoration or the complete Basic Set.
