# Interrogation source correction (B202)

The uploaded Characters third printing B202 specifies one Quick Contest of
Interrogation against the prisoner's Will for each question. Both parties can
succeed; the winning margin decides the result. No B349 Regular Contest score
normalization or repeated pair belongs to this procedure. A win obtains a
truthful answer; a tie or loss permits silence or a lie. Losing by more than five
requires a good, believable lie, roleplayed by the GM. All dice are secret.

The current pure procedure and fresh immediate, NPC and task consumers now use
that Quick Contest. A hand-entered skill 16/Will 16 witness keeps both targets at 16
and decides a both-success pair immediately. Boundary tests distinguish losses
by five and six. The believable-lie classification is private GM information;
no false answer text or false world fact is generated.

Every fresh public question result has the same `interrogation-resolved` label
and generic GM-roleplay prompt. Private effect IDs, winning margins and dice do
not enter the player's registered campaign or stream projections. An authored
truthful disclosure can still reveal only a preexisting fact known by the
subject, and losing questions cannot disclose truth via a `withstood` policy.

This does **not** close B202 secrecy. The existing World knowledge architecture
adds a visible fact entry only on a truthful disclosure. Paired actual service
and campaign/stream tests record this difference: a true answer can produce a
known-fact entry while a believable lie cannot. That remains a possible truth
oracle. Equally nonrevealing roleplayed answer/claim handling is still required;
writing false World facts would not be a valid fix.

Replay uses independent private semantics. Fresh immediate captured contexts
carry `interrogation_quick_contest`; absent values retain their recorded Regular
Contest. Automatic NPC and task checkpoints use social generation 4. Versions 0–3
retain their former Regular Interrogation, including the distinct pre/post-B349
normalization in versions 0–1 and 2–3. Generation 4 retains the version 3 self-control
correction and the historical version 2 Regular Contest behavior for other rules.
Tests reexecute old and new commands from original genesis on both database
backends, including pending director choices and task checkpoints.

The existing explicit coercion choice remains separate: B202 severe threats +3
or torture +6, with the Callous +1 derived from the approved build. Authored physical
injury/fatigue costs and B494 retribution are committed through existing reducers
and standing events; no universal torture cost is invented. This correction does
not add unrelated reaction/standing bonuses to the Quick Contest.

Whole-skill compliance remains open. In addition to the knowledge-entry secrecy
oracle, B202 requires a five-minute question lifecycle. Current immediate and
checkpoint consumers resolve at their existing command time without a 300-second
question reservation, timed completion, interruption or repeat-question history.
The entry's loyalty −5, Low Empathy −3 and lengthy-interrogation +2 modifiers also
lack explicit reviewed bindings here. Closure needs durable question identity,
a current start/completion boundary with no premature roll/disclosure, source
modifiers and session duration, GM-only answer/claim handling, and replay of
completion/interruption on both stores. Existing inventory availability is not
evidence that these omitted obligations are complete.

Separately, Characters third B197 player-versus-player Gambling specifies a
Regular Contest until someone wins. Its pure knowledge dispatcher now selects
that existing reducer, with a two-round both-success witness. Detect Lies and
Body Language retain their Quick Contests. This does not implement house odds,
cheating contests, monetary stakes or a persisted Gambling host.
