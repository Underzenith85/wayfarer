# Core Growth and Shrinking runtime

This implementation was checked against Characters third printing B9, B19,
B58, B85 and Campaigns fourth printing B402. It is engine implementation
work for #758, not an end-to-end Basic Set certification claim.

`SizeFormService` uses the existing actor-control, revision, command receipt,
and checkpoint pipeline. It chooses a purchased size delta, interrupts a
change at its current size, or returns to native size. A combat Ready permits
one SM change in the next elapsed second. Waiting outside combat advances the
chosen continuous change at one SM per second. Combat does not bank skipped
seconds or continue changing merely because an opponent took turns.

Committed size affects authoritative SM, the B19 table dimension, exact height
ratio and B85 Shrinking weight ratio. Shared combat reach, movement, ranged
body targeting, unarmed/innate damage, active innate DR and HP use that current
state. Character feat and combat-result rounding occurs after exact rational
scaling. HP loss keeps its exact fraction across intermediate forms and return;
rounding a temporary HP projection does not permanently create a new wound.

Core Growth grants no free ST. The approved character must already have the ST
B58 requires for the purchased maximum height. Core Shrinking retains ST and
leaves all gear behind, including clothing and container contents. Ownership
remains with the actor. Mapped fights record the actual current position;
otherwise items record the authoritative world location. Grounded container
contents cease counting as carried load or usable equipment. Reversion leaves
gear on the ground; retrieval at its recorded location does not equip it.

The compiler now admits the printed Shrinking 12 example. A character with
native HP 100 can become SM -12, with height 1/100, weight 1/1,000,000,
and HP 1. The existing integer-health engine cannot represent a rounded HP
maximum below one; that range is explicitly rejected pending a coherent
fractional/zero-health implementation. It is not clamped to HP 1.

Growth above the existing B402 reach table, Size-limited extra ST, Maximum
Size Only, Carry Objects and other size enhancements, combinations with an
active alternate body, and automatic fitting/clearance or multi-hex footprint
changes need separately verified protocols. The current body-size projection
does not invent a Growth mass formula. These boundaries remain unverified;
this change does not promote source certification rows or close #758's
remaining coverage work.
