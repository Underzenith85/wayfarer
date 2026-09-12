"""The words a combat command is written in: maneuvers, postures, defenses, facing."""

from __future__ import annotations

from typing import Literal

Facing = Literal["north", "east", "south", "west"]
Posture = Literal["standing", "crouching", "kneeling", "crawling", "sitting", "prone"]
Maneuver = Literal[
    "do_nothing",
    "move",
    "ready",
    "change_posture",
    "attack",
    "wait",
    "concentrate",
    "aim",
    "evaluate",
    "feint",
    "all_out_attack",
    "all_out_defense",
    "move_and_attack",
]
Defense = Literal["dodge", "parry", "block", "none"]
