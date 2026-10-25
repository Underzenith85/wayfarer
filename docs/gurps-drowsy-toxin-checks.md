# Awake drowsiness from a trusted toxin

The selected Campaigns fourth printing B428 specifies −2 to DX and IQ for an
awake drowsy subject. The former generic toxin check adapter applied only −1
to IQ, borrowing Coughing's IQ penalty and omitting DX. The new bounded producer
uses the actual B437–440 toxin delivery, resistance and condition lifetime,
followed by the existing ordinary check consumers.

`DrowsyToxinService` admits only a scenario-bound, non-damaging, non-addictive,
non-depressant single-dose drowsy profile with a fixed positive condition duration
shorter than two hours. The trusted resolver supplies its private profile and
delivery evidence; the command supplies only actor, operation and exposure IDs.
Approved HT supplies resistance. A failed resistance starts the authored condition
duration; a successful resistance adds no condition or visible condition timestamp.
An overlap with a pending or current drowsy dose refuses. Profiles causing damage,
multiple cycles, margin-based duration, or longer episodes remain unsupported by
this service, as do treatment/discovery commands.

Exposure captures a private per-source generation. Current awake drowsiness
contributes −2 to ordinary DX/IQ checks and their skill consumers, once rather
than once per record. B421 propagates temporary IQ penalties to Will, Per and
their governed skills; ordinary checks against those attributes also receive −2.
B421 defensive attribute exemptions remain intact; HT and ST are unaffected. Expiry uses the
authoritative game clock. Approved purchases and stored base statistics remain
unchanged. Historical command generations retain the original values of
unmarked toxin exposures, including their resistance/condition behavior; they
are replay compatibility, not source-correct evidence. Fresh private generation
2 additionally corrects ordinary check modifiers for existing unmarked drowsy
conditions, as described in [condition propagation](gurps-condition-attribute-propagation.md).
It does not rewrite those sources or their earlier resistance receipts.

The GM must hold both the current campaign director seat and deployment trust.
The service uses the existing atomic command pipeline. Its captured resolver
facts survive exact retries after restart and seeded reexecution without invoking
a replacement resolver. Invalid authority, stale revision, unsupported profile,
failed delivery and failed settlement leave no partial exposure or condition.
The private replay family reexecutes actual exposure, resistance and downstream
ordinary checks from the original campaign genesis.

`tests/test_drowsy_toxins.py` exercises SQLite and PostgreSQL. A source-authored
one-minute awake episode reduces purchased Chemistry 10 to 8: total 9 fails and
reveals no fact. At the exact expiry, the same roll succeeds and reveals the
scenario fact. Actual Broadsword 13 becomes 11, so total 12 misses rather than
inflicting injury. Successful HT resistance leaves the ordinary check at 10.
An actual Hearing check also changes from Per10 to 8. A canonical reducer
diagnostic after the persisted toxin producer spends ordinary FP to zero, then
checks continued exertion at Will8 instead of 10: total 9 causes actual collapse
while drowsy and permits continuation after expiry. That diagnostic does not
claim persistence of the later fatigue commands.
The one-minute duration is a trusted scenario input, not a duration printed in
B428. All tests remain inside the interval before a two-hour inactivity check.

B428's self-control penalty now has a separate
[supported condition consumer](gurps-condition-self-control.md), including
normalization with simultaneous survival drowsiness. Periodic Will checks while
inactive, sleep on failure, and longer toxin episodes still need their own
supported timing and wake/sleep contracts. This bounded correction
does not admit Drowsy as a Symptoms or Affliction construction, nor Euphoria,
Nauseated or Pain. Their missing producers and consumers remain owned by #763.
No source-ledger, capability or certification status is promoted.
