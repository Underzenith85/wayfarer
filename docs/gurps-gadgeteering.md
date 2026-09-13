# Gadgeteering projects

Issue #525 extends the persisted invention lifecycle with the gadgeteering rules
from the selected fourth-printing *Campaigns*, pp. B475-B477, and the two-level
Gadgeteer construction from the selected third-printing *Characters*, pp. B56-B57.
Rulebook wording is not reproduced here.

## Capability and campaign gates

`trait:advantage:gadgeteer` is a two-level, compiler-backed trait. Its approved
level selects ordinary Gadgeteer or Quick Gadgeteer capability. Project commands
carry no capability flag: the transition derives it again from the canonical
approved build. `InventionRules.permitted_methods` separately records the GM's
campaign decision, and Quick Gadgeteering permission requires ordinary
Gadgeteering permission. Ordinary invention remains available without the trait.

## Schedules and resources

All three methods use `InventionProject`, `InventionWork`, the shared resource
ledger, CAS revisions, receipts, and serialized outcomes. Ordinary work retains
its authored full-time schedule. Regular gadget work retains the authored labor
duration but records an interruptible gadget schedule. Quick concept and
prototype durations are calculated from recorded dice and complexity; the dice
are stored in the work item and outcome.

Regular gadget facilities use the B475 complexity/TL table, including the
explicit similar-facility reduction. Quick purchased facilities and prototype
parts use the B476 reductions. Retail price is adjusted from campaign TL to the
device's native TL before prototype or production calculations. Authored
improvised materials are preflighted and consumed in the same atomic begin
transition as money. A lost-response retry returns the receipt without charging,
consuming inventory, advancing time, or drawing schedule dice again.

## Defects and discovery

A successful gadget prototype cannot acquire a major bug. When minor bugs are
possible, the engine derives their count from the recorded prototype entropy.
Higher-TL bugs then receive recorded 3d selections from the B476 table. Device
context such as powered/unpowered and weapon/non-weapon is authored in the
blueprint, so narration cannot choose a more convenient result.

Each `GadgetDefect` has a stable ID, typed kind, selecting dice, table total,
typed magnitude where applicable, and discovery state. Testing records the
shared-clock discovery time. These facts survive JSON persistence and command
replay. Exact downstream execution of side-effect content and world-specific
unwanted-attention consequences remains unavailable until a campaign supplies a
separately authored effect binding; the project never invents either from prose.

## Adventure activity and other users

Regular and quick gadget work may be paused before its deadline. Pausing records
the exact remaining labor, releases the actor from the full-time activity guard,
and lets shared adventure time advance. Resuming checks current activity conflicts
and creates a new deadline from the remaining labor; costs and schedule entropy
are not repeated. Encountered-gadget analysis and modification are authored
project kinds with the B477 regular/quick schedules and the corresponding
concept/prototype checks.

Completed-gadget access is explicit. A non-gadgeteer may use or repair a device
only when its authored `NonGadgeteerAccess` permits that operation, and may
reproduce it only when reproduction was separately permitted. Analyze, modify,
and invent always require compiled Gadgeteer capability. Access never adds a
trait or grants invention capability.

The prerelease engine version is unchanged.
