"""Shared demo contracts. Runtime validation remains at the service boundary."""
from typing import Literal, NotRequired, TypedDict

Action = Literal['observe', 'talk', 'sneak', 'rest', 'ask']

class Character(TypedDict):
    name: str
    concept: str
    attributes: dict[str, int]
    skills: dict[str, int]
    traits: list[str]

class Roll(TypedDict):
    dice: list[int]
    total: int
    target: int
    success: bool
    critical: Literal['success', 'failure'] | None

class Message(TypedDict):
    role: str
    text: str
    roll: NotRequired[Roll | None]
    action: NotRequired[Action]
    flavor: NotRequired[str]

class Campaign(TypedDict):
    id: str
    revision: int
    rules: str
    character: Character
    scenario: dict[str, str]
    hp: int
    fp: int
    minutes: int
    location: str
    inventory: list[str]
    discoveries: list[str]
    flags: list[str]
    complete: bool
    messages: list[Message]

class Event(TypedDict):
    input: str
    action: Action
    outcome: str
    roll: Roll | None
