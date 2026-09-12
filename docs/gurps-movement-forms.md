# GURPS movement and form traits

Wayfarer exposes the Basic Set movement/form family through the optional
`package:gurps-basic-movement-forms@1.0.0` package. Campaigns must permit the
Characters source, enable supernatural purchases, and supply every runtime
hook published by `wayfarer.engine.rules.movement_forms.RUNTIME_HOOKS`.

The package keeps construction and execution inseparable:

- fixed, leveled, variable-template, limb-scope, terrain, material-rarity, and
  locomotion-form costs are selected from the pinned registry;
- caller-supplied parameters and modifiers are checked against the exact trait;
- an approved build projects movement, time-rate, lifting, jumping, climbing,
  falling, size, leg, and manipulator consequences;
- Alternate Form, Morph, Growth, Shrinking, Shadow Form, and
  Insubstantiality use typed, actor-authorized start/resolve/cancel/interrupt
  commands and immutable resource events;
- active effects carry the approved build revision and survive serialization;
  observer views use the world's knowledge-filtered perspective.

`advantage:shapeshifting` is the source heading for Alternate Form and Morph,
not a separate zero-cost advantage. The compiler therefore rejects it and
requires one of those concrete constructions. Inventory rows remain partial
until the selected-printing review tracked by #191 is reconciled.
