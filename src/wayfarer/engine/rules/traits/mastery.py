"""Source-bounded Weapon Master constructions (Characters third printing B99)."""

from typing import Final

from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.errors import ValidationError

# B271-276 muscle-powered catalog rows. Energy/powered weapons and guns cannot
# inherit mastery merely because their authored mode resembles a melee weapon.
MUSCLE_WEAPONS: Final = frozenset(
    """
light-club broadsword axe hatchet throwing-axe mace small-mace pick blackjack
thrusting-broadsword bastard-sword katana thrusting-bastard-sword cavalry-saber
morningstar nunchaku large-knife small-knife wooden-stake dagger kusari lance
glaive naginata halberd poleaxe rapier saber baton shortsword cutlass short-staff
smallsword spear quarterstaff javelin long-spear maul great-axe scythe warhammer
flail greatsword thrusting-greatsword whip-1-yard whip-2-yard whip-3-yard whip-4-yard
whip-5-yard whip-6-yard whip-7-yard longbow regular-bow short-bow composite-bow
crossbow prodd sling staff-sling blowpipe-dart light-shield small-shield
medium-shield large-shield spiked-light-shield spiked-small-shield
spiked-medium-shield spiked-large-shield iron-light-shield iron-small-shield
iron-medium-shield iron-large-shield
""".split()
)
SWORDS: Final = frozenset(
    """
broadsword thrusting-broadsword bastard-sword thrusting-bastard-sword katana
cavalry-saber rapier saber shortsword cutlass smallsword greatsword
thrusting-greatsword
""".split()
)
BLADES: Final = SWORDS | frozenset(
    """
axe hatchet throwing-axe large-knife small-knife dagger glaive naginata
halberd poleaxe great-axe scythe
""".split()
)
SHIELDS: Final = frozenset(w for w in MUSCLE_WEAPONS if "shield" in w)
# Printed named examples remain bounded choices; arbitrary author-defined
# thematic classes (for example "ninja weapons") need a separate GM binding.
SCOPES: Final = {
    **{weapon: (20, frozenset({weapon})) for weapon in sorted(MUSCLE_WEAPONS)},
    "broadsword-and-shield": (25, frozenset({"broadsword", "thrusting-broadsword"}) | SHIELDS),
    "rapier-and-main-gauche": (25, frozenset({"rapier", "large-knife"})),
    "fencing": (30, frozenset({"large-knife", "rapier", "saber", "smallsword"})),
    "knightly": (30, frozenset({"broadsword", "thrusting-broadsword", "mace", "lance"}) | SHIELDS),
    "swords": (35, SWORDS),
    "bladed": (40, BLADES),
    "one-handed": (40, MUSCLE_WEAPONS),
    "all": (45, MUSCLE_WEAPONS),
}


def scope(options: TraitOptions) -> str:
    parameters = dict(options.parameters)
    selected = parameters.get("weapon-scope", "all" if parameters.get("point-cost") == 45 else None)
    if not isinstance(selected, str) or selected not in SCOPES:
        raise ValidationError("Weapon Master requires a supported authored weapon scope")
    if parameters.get("point-cost") != SCOPES[selected][0]:
        raise ValidationError("Weapon Master point cost does not match its purchased scope")
    return selected


def covers(options: TraitOptions, weapon_id: str, hands: int, skill_id: str) -> bool:
    selected = scope(options)
    weapon = weapon_id.removeprefix("equipment:")
    if selected in {"fencing", "rapier-and-main-gauche"} and weapon == "large-knife":
        return skill_id == "skill:main-gauche"
    return weapon in SCOPES[selected][1] and (selected != "one-handed" or hands == 1)
