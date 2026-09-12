# GURPS Basic Set mental and spirit traits

Issue #236 is implemented as a pinned 45-entry construction package. The package preserves
the Characters third-printing source identity and validates levels, required selections,
special modifiers, exclusions, and variable costs before a build can be approved.

Approved builds project concrete mind-shield, concentration, Higher Purpose, Terror,
spirit/ancestor/past/future contact, and psi-static capabilities. Runtime powers use
GM-authored channels with explicit actors, targets, locations, resistance rolls, fatigue
costs, durations, interruption policy, and private fact IDs. The reducer enforces actor
authority and compare-and-set revisions, charges canonical FP, records persistent effects,
reveals only authored facts, and provides identical-command replay after restart.

The inventory remains `partial` only because source certification issue #191 is still open;
the runtime blocker #236 is removed from every covered row.
