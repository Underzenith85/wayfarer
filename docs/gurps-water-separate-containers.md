# Purify Water: measured separate containers

Characters B253 supports removing impurities from water poured through a ring
into a receiving container, with one roll covering an uninterrupted flow. This
private host admits one finite ordinary batch consisting of complete physically
separate containers. It does not select constituents from a mixed container.

A trusted seated GM submits `declare-water-parcels` as themselves. The command
references existing canonical world measurement facts and the complete ordered
set of actual child containers belonging to the source assembly. It cannot supply
numeric caster-authored geometry. The source aggregate must exactly equal the
sum of the separate containers, including its pure-water quantity. A container is
recorded wholly pure or impure; partial internal composition is unsupported.

The source, receiver and intact ring must currently belong to the caster and
occupy the caster's location. Measured horizontal positions must agree with the
canonical water placements. Measured heights descend from source through ring to
receiver. A positive measured stream width must fit the positive ring opening.
Only the printed ordinary 5–10 seconds per gallon is admitted; diameter does not
imply an accelerated rate. High spell skill cannot shorten the captured physical
flow duration; the normal low-skill casting-time increase and skill-based energy
reductions still apply. Ambiguous measurements, noncanonical or fractional
units, missing children, nested containers, aliasing and cyclic custody fail
before randomness. Objects also represented by inventory Items require their
own concrete item adapter and are refused here.

An immutable private channel selects `parcel_flow_id`. Its requested whole-gallon
volume must be an exact prefix of complete child containers. Source composition,
all referenced measurement values, object custody and topology are checked again
at each live casting transition. Existing receiver capacity and purity checks
still apply. Successful Purify subtracts the measured original pure quantity
from the source, adds the complete purified volume to the receiver, and marks
exactly the poured child containers empty. These world facts, water events,
fatigue, spell result and command receipt commit together. Failure leaves material
and world facts unchanged under the existing casting failure rules.

The observation ends after one accepted finite batch; it cannot be replaced or
reused. Remaining stock can receive a new observation and channel, including the
existing empty children, without duplicating the aggregate volume. This does not
claim a second skill roll during one continuous unchanged stream: the original
batch has ended. Exact retries return their original receipt and material state.
The observation and material procedure records remain GM-only in player event
projections. Full seeded reexecution includes the private observation command and
its canonical world facts.

Once observed, the source assembly continues to require its physical material
adapter even after that observation ends. Generic whole-body Create, Destroy or
Purify mutations are refused before randomness, because they would diverge the
aggregate from its child-container facts. Receiver-only legacy operations remain
supported. The [measured finite collection adapter](gurps-water-measured-collection.md)
can refill one empty clean child from an entire authenticated homogeneous
vessel, updating child facts and the aggregate together. Other refill and
destruction paths still require concrete container adapters under #981. A valid parcel flow also
cannot refill a receiver that is itself another observed source assembly. This
is rechecked at completion when such an observation arrives during casting.

Historical channels omit `parcel_flow_id` and retain their previous serialization
and material behavior, including rejection of unmodeled heterogeneous partial
sources. Public spell schemas and source certification rows are unchanged.

Issue #981 remains open: fractional volumes, heterogeneous constituents within a
single container, accelerated ring flow, midair collection/refill, broader
mist/fire geometry and authorized item casting are not implemented by this slice.
