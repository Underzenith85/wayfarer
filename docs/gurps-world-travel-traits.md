# GURPS Basic Set world-travel traits

Issue #238 implements the complete Jumper, Snatcher, and Warp family as a pinned package.
Build approval requires each trait's kind, capacity, or reliability selection and validates
all supported special modifiers against the exact Characters third-printing source pin.

Runtime travel uses authored routes with stable origins, destinations, readiness times,
IQ targets, preparation and distance modifiers, FP costs, object identities and weights,
and explicit critical-failure destinations. Successful Jumper and Warp uses move the
canonical actor entity; Snatcher transfers the canonical unowned object to the actor.
Failures and misjumps are recorded without inventing destinations. The reducer enforces
actor authority, due time, compare-and-set revisions, exact build bindings, context
continuity, identical-command replay, and restart-safe history.

Inventory rows remain `partial` for source certification #191; runtime blocker #238 is
removed from all three entries.
