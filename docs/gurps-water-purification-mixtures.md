# Water purification and recontamination boundary

Selected source: Characters third printing, B253 (physical PDF page 255).

The large container/ring exception does not prescribe a faster numeric casting
rate, define a large-vessel threshold, or give an aperture/flow/time formula.
Existing water records contain receiving capacity but no physical ring identity,
aperture or measured continuous flow. They cannot justify an invented accelerated
rate. Admission of that exception would need current canonical vessel and ring
facts, a supported physical flow observation and its elapsed time, actual source
quantity and receiving capacity, and interruption of the continuous flow. This
wave does not pretend those facts exist or assign a faster source rate.

The selected next variant has an exact qualitative consequence: water is pure
until recontaminated. Pouring pure water into an explicitly mixed vessel already
containing impure water adds the actual volume, but does not purify that vessel.
The mixed liquid remains impure. No numerical dilution threshold, contaminant
concentration, toxin, injury or damage follows from the B253 wording.

The private opt-in is an operation intent to mix the receiving contents. Existing
pure/impure gallon facts supply the actual contamination observation. Clean or
empty receivers stay pure; an impure receiver becomes one mixed impure volume.
The canonical world object's identity, placement and custody remain unchanged.
Create Water still costs 2 per gallon; Purify Water still costs 1 per gallon and
uses the supported ordinary 5–10 seconds per gallon. Failed casting changes no
material and retains the shared ordinary failure payment.

The explicit private `allow_receiver_mixing` intent is captured in the immutable
per-cast commitment. Its false default is omitted from serialization; historical
commands keep the previous receiving-vessel restriction and exact commitment
bytes. The intent is valid only for Create Water and Purify Water. Incoming pure
water is mixed with the current receiving contents only after successful casting.

Fourteen actual host cases cover both spells across SQLite and PostgreSQL:
success/failure with independent cost/time and source/receiver consequences,
authority, stale revision, exact retry after restart, and full seeded command
re-execution. Additional domain, legacy and public schema tests confirm current
capacity, qualitative impurity, the historical commitment digest and unchanged
public spell models. The repaired Seek Water base is retained without edits to
its binding, discovery or view seams.

Partial heterogeneous-source flow composition, fractional volume, accelerated
large-ring pouring, airborne globes and deep/refilling water remain unverified.
Complete heterogeneous-source transfer and admitted one-gallon mist now have
separate execution evidence. See the reconciled
[four-spell baseline and follow-up contracts](gurps-spell-water-effects.md).

Remaining Water material and geometry contracts are tracked in
[issue #981](https://github.com/Underzenith85/wayfarer/issues/981).
