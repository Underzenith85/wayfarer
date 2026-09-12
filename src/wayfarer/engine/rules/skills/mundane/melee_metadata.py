"""Shared melee weapon-class metadata, independent of executable procedures."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class WeaponClass:
    """Mode properties a melee skill is permitted to claim."""

    hands: tuple[int, ...] = (1, 2)
    permits_parry: bool = True
    fencing: bool | None = None
    permits_unbalanced: bool = False


ONE_HAND = WeaponClass(hands=(1,))
TWO_HAND = WeaponClass(hands=(2,))
FENCING = WeaponClass(hands=(1,), fencing=True)
UNBALANCED = WeaponClass(permits_unbalanced=True)
ONE_HAND_UNBALANCED = WeaponClass(hands=(1,), permits_unbalanced=True)
TWO_HAND_UNBALANCED = WeaponClass(hands=(2,), permits_unbalanced=True)
NO_PARRY = WeaponClass(hands=(1, 2), permits_parry=False)

WEAPON_CLASSES: Final = MappingProxyType(
    {
        "skill:axe-mace": UNBALANCED,
        "skill:brawling": ONE_HAND,
        "skill:broadsword": UNBALANCED,
        "skill:flail": ONE_HAND_UNBALANCED,
        "skill:force-sword": ONE_HAND,
        "skill:force-whip": ONE_HAND_UNBALANCED,
        "skill:jitte-sai": ONE_HAND,
        "skill:knife": ONE_HAND,
        "skill:kusari": UNBALANCED,
        "skill:lance": NO_PARRY,
        "skill:main-gauche": FENCING,
        "skill:monowire-whip": ONE_HAND_UNBALANCED,
        "skill:polearm": TWO_HAND_UNBALANCED,
        "skill:rapier": FENCING,
        "skill:saber": FENCING,
        "skill:shortsword": ONE_HAND,
        "skill:smallsword": FENCING,
        "skill:spear": UNBALANCED,
        "skill:staff": TWO_HAND_UNBALANCED,
        "skill:tonfa": ONE_HAND,
        "skill:two-handed-axe-mace": TWO_HAND_UNBALANCED,
        "skill:two-handed-flail": TWO_HAND_UNBALANCED,
        "skill:two-handed-sword": TWO_HAND_UNBALANCED,
        "skill:whip": ONE_HAND_UNBALANCED,
    }
)
