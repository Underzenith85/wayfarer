# Ground diving Steps through the fragment task host

Campaigns B368 defines a Step from current Move. B377 permits a chosen dodge and
drop toward nearby cover, resolving a failed dodge before the movement. The
canonical ground Step consumer now runs under a trusted private generation for
`PrepareOpponentFragment`, `AmendFragmentResponses` and `ChooseOpponentFragment`.
These typed task routes already carry the actual blast responses and pending
continuation; public schemas and response declarations remain unchanged.

The existing `task_combat_protocol_features` namespace admits `ground-dive-step`
only for these three fragment routes. Ordinary task defense producers continue to
admit only `grenade-fuse`. Mixed route features, unknown or duplicated features,
non-list values, wrong host operations and noncanonical bytes reject. Full typed
fragment commands are validated before recorded features are used. The actual
open, amendment and choice handlers run in a bounded engine feature scope.
Historical recorded absence selects the old scope even when defaults change.

Actual both-store tests demonstrate blocked opening refusal before RNG, successful
and failed dodge ordering with movement applied at fragment settlement, durable
public retries, current occupancy and Move refusal during amendment and selection,
and rejection without campaign/history/event changes. Seeded open, amendment and
choice reexecution verifies both historical absence and new feature receipts.
Public schemas, the original blast cause, and grenade-fuse capture remain intact.

This is the fragment task host join for the existing mapped ground Step consumer.
It does not add aerial or aquatic geometry, catch mechanics, airbursts, secondary
blasts or burning chains. Whole issues #878 and #869 remain open.
