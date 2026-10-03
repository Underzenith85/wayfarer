# Selected Great Haste casting Step

Basic Set B236 requires consecutive Concentrate maneuvers and the casting roll at
the end of the final maneuver. B366 permits Step movement and ordinary active
defenses while concentrating; defense, injury and other distraction require
Will−3. B368 supplies the existing Step allowance. Private source PDFs and
extracted rules text remain outside this repository.

The private `StepCastGreatHaste` carrier selects a square destination/facing or a
hex path/facing. It records generation 2; existing Great Haste commands retain
generation 1 and their exact request identity. The original private command
adapter and public combat/spell schemas are unchanged. Recorded absence still
retains the historical outside-combat route.

This bounded route performs a selected Step before the Concentrate action. It
requires the existing known, currently visible nonself subject both before and
after the Step. Entering line of sight from an unseen starting subject, Steps
after or split around the action, posture substitution and general subjective
casting for other spells remain unsupported. No destination is inferred.

Movement, hex terrain, elevation, facing, occupied positions, Wait interruption,
injury start/end, exertion, native maneuver opportunities and shared clock use the
canonical combat path. Casting advances only after accepted movement and Wait
interruption detection, before final physiology and turn settlement. The last
Concentrate checks the moved range and current approved source, then rolls and
pays; its ten-second effect uses the real completion clock.

A triggered Wait saves the accepted movement prefix and a private casting lease.
It spends no casting second, casting dice or FP while paused. Existing
`ResumeInterruptedTurn` resumes the saved remaining movement without accepting
new geometry or rebinding the source command. Cancellation or lost concentration
retires the lease and settles the original maneuver once. Fresh casting is
blocked during the pending response.

The existing combat Will−3 roll supplies a private durable resolution witness
only for an active selected-Step lease. Its actual check, adjudicated HP, cast
and lease identity, effect digest, and latest spell event identity/digest must
match before acknowledging a pending spell distraction. A missing or mismatched
witness, later HP loss, or a newly appended identical distraction event preserves
the spell's pending check. This avoids repeating a resolved combat check or
silently discarding a later unadjudicated distraction. These ledgers are rejected
in genesis and absent from player, spectator and GM projections.

Actual-host tests exercise selected square/hex movement, moved completion range,
Wait pause/restart/resume/cancellation, active defense Will−3 success/failure,
actual injury and HP resolution, exact retries, altered-request refusal, seeded
pause and resume reexecution, terrain rollback, generation pairing and private
ledger admission. Adversarial witness cases use deliberately changed store
fixtures to verify refusal to acknowledge mismatched state; they do not claim a
public way to author those private records. Existing casting and ordinary
unarmed Wait/concentration tests protect historical behavior. Split/after Steps
and named-plus-Step combinations remain unverified under [the remaining Movement host carriers (#980)](https://github.com/Underzenith85/wayfarer/issues/980). See
`gurps-haste-construction-acceptance.md` for the written #797 baseline case map.
