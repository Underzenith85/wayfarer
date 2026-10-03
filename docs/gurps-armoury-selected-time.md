# Selected Armoury repair time

Campaigns B346 provides generic time modifiers. B484 explicitly applies those
modifiers to minor repairs; B485 uses the same attempt rules for major repairs,
with its separate difficulty and parts requirement. This consumer covers the
bound Armoury weapon and body-armor restoration procedures.

A private, actor-authenticated `SelectRepairTime` command selects a discrete work
method for one future repair start command ID. The ordinary finite repair attempt
uses the printed generic methods without an invented mandatory GM approval.
The selector validates current ownership, availability, approved specialty/TL,
tools, damage and effective skill before any parts die or stock consumption.
Its work plan is immutable and cannot change a pending attempt.

The 1,800-second baseline supports these derived choices:

| Method | Duration in seconds | Skill adjustment |
| --- | ---: | ---: |
| Ordinary | 1,800 | 0 |
| Extra 2 / 4 / 8 / 15 / 30 times | 3,600 / 7,200 / 14,400 / 27,000 / 54,000 | +1 / +2 / +3 / +4 / +5 |
| Haste 10 / 20 / 30 / 40 / 50 percent | 1,620 / 1,440 / 1,260 / 1,080 / 900 | −1 / −2 / −3 / −4 / −5 |
| Haste 60 / 70 / 80 / 90 percent | 720 / 540 / 360 / 180 | −6 / −7 / −8 / −9 |

Unprinted rates and discretionary cinematic instantaneous repair are unavailable.
The existing repair equipment admission also refuses unsupported tool operating
limits. These methods do not infer work shifts, assistants or long-project rules.

The actual start rechecks the selection's actor, item, condition and equipment
profile, adds the recorded adjustment to the captured skill, and pins the deadline
relative to that start. Effective skill must reach the source minimum after the
selected adjustment; extra time can legitimately make an otherwise unavailable
attempt legal. Completion uses that captured skill and deadline, rechecking
current equipment. Clock time before start and extra passive waiting after start
never increase the modifier.

Parts assessment remains independent. Selected starts consume its immutable
quantity when present; unassessed starts retain the historical preflight and
single parts-die stream. Cancellation preserves the recorded selection and parts
accounting. A later start ID inherits no choice from the cancelled attempt.

The plan exists only in private events and an optional private repair-task field.
Its absence is omitted from historical task serialization. Existing public repair
commands, default duration/skill, and recorded random streams are unchanged.

Actual-host acceptance tests exercise every printed method for weapon and armor,
with independent duration, target, margin and HP oracles. They also cover selected
major repairs and conservation, the effective-skill boundary, premature completion,
passive time, authority and privacy, current setup refusal, cancellation, checkpoint
rollback, exact byte-sensitive retries, reload and full seeded reexecution on
SQLite and PostgreSQL. Whole issue closure still requires the coordinator's
independent review and exact-head remote CI and merge verification.
