# Water in a carried wineskin

Create Water can now fill one actual canonical `equipment:wineskin` InventoryItem
with one permanent gallon of pure water. This ordinary caster route uses the
approved Create Water build and its normal one-second cast, two FP successful
cost, failure cost, authority, immutable cast plan and retry handling. It is not
enchanted-item casting.

The supplied Basic Set rules identify a wineskin as a one-gallon vessel weighing
0.25 lb empty, and its gallon of water as 8 lb (B253, B288). A trusted director
records the current empty, intact, physically touched vessel, its owner, ancestor
containers and exact pinned catalog profile. No inferred World object substitutes
for the live inventory item. Only the observed owner's Create Water cast can use
this receiver. Changed custody, container ancestry, item state or catalog profile
refuses before the casting roll. The full gallon is previewed through canonical
receiver, ancestor and owner capacity validation before dice or fatigue spending.

The private material receipt contributes 8,000 millipounds to the actual item.
Canonical inventory mass, every ancestor's contents and actor encumbrance use that
same quantity. A filled wineskin weighs 8.25 lb. Whole-item transfers preserve its
ID and material exactly once; lawful world drop and retrieval remove and restore
carried mass without deleting the water. Generic deletion, expenditure, splitting,
disabling or incompatible profile replacement fails closed until a material
adapter can account for the contents. World WaterBody aliases and duplicate
material receipts refuse. Private observation and execution records are rejected
from authored genesis and omitted from player and spectator projections.

Focused tests cover both SQLite and PostgreSQL: actual success/failure cost and
material, actual transfer/retry/reload, current receiver guards, own/ancestor/owner
capacity, rollback, spectator authority/privacy, and original-genesis seeded
reexecution including drop/retrieve and a real combat Move. The independent
movement oracle starts at 19.25 lb, Move 5; the gallon raises the load to 27.25 lb,
Move 4. The owner inventory projection reports the same canonical total. The
existing world-equipment replay family is now dispatched for its already recorded
drop/retrieve commands; its command and event formats are unchanged.

[Issue #981](https://github.com/Underzenith85/wayfarer/issues/981) remains open.
This receiver does not implement quart vessels, fractional liquid, drinking,
pouring/spilling inventory contents, partial quantities, damaged-vessel leakage,
midair collection, natural refill or accelerated ring rates. Such operations must
conserve the material and its mass through an actual supported consumer. Public
schemas and historical unselected World/Staff casting paths are unchanged.
