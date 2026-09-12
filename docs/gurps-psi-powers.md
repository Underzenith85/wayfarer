# GURPS Basic Set psionic powers

Issue #240 implements the six Basic Set psionic power groupings: Antipsi, ESP,
Psychic Healing, Psychokinesis, Telepathy, and Teleportation. The pinned package
defines the five 5-point/level Talents with the source default cap of four;
Antipsi intentionally has neither a Talent nor a power discount.

Approved loadouts bind purchased advantages to the exact source member lists,
the -10% power modifier, allocated levels, and required variants such as
Para-Radar, Force Field, Telesend, and the allowed telepathic Maledictions.
Talents may exist without abilities (latency), while adding an entirely new
power requires explicit GM permission. Talent bonuses and resistance are
adapted into the existing ability context without forking its ability engine.

Antipsi Neutralize and Psi Static use authored targets and locations, actor
authority, compare-and-set revisions, optional expiry schedules, explicit
deactivation, and restart-safe event history. Telepathy rejects nonliving or
nonsentient targets; suppression fails before the shared ability service runs.

Inventory rows remain `partial` for source certification #191; runtime blocker
#240 is removed from all six powers.
