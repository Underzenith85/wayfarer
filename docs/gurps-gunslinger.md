# Gunslinger ranged attack binding

Selected source: Basic Set Characters, Fourth Edition, third printing,
printed B58. Scope is the Basic Set advantage only.

Purchased `trait:advantage:gunslinger` is projected from the approved build
and adds weapon Accuracy to actual ranged attack targets. The selected weapon
mode must use a concrete Beam Weapons, Gunner, Guns, or Liquid Projector
specialty. Muscle-powered weapons, including Gunner (Catapult), and Artillery
are excluded.

One-handed modes with printed RoF 1–3 receive full Acc. Two-handed modes or
modes with RoF greater than 3 receive half Acc, rounded up. RoF classification
comes from the pinned weapon mode; firing a single round from a high-RoF mode
does not reclassify that mode. A valid Aim supplies its ordinary full Acc,
brace, scope, and additional-time benefits instead of stacking Gunslinger.
Move and Attack retains its ordinary Bulk penalty. No supplement-specific
movement, close-combat, Fast-Draw, or recoil benefits are introduced.

`tests/test_gunslinger.py` checks real numeric attack targets for one-handed,
two-handed, rapid-fire, beam, and liquid stream modes; ordinary unowned controls;
Bow and Crossbow exclusions; Aim time, scopes and bracing; and the retained Move
and Attack penalty. Odd and even Acc cases verify rounding. Replay checks verify
the same committed state.

The inventory row gains these specific evidence paths without promoting its
implementation status or claiming complete certification of other variants.
