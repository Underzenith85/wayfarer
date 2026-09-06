"""Closed, GURPS-inspired prototype rules. Not a complete GURPS implementation."""

VERSION = "wayfarer-lite-1"
BUDGET = 100
ATTR_COST = {"ST": 10, "DX": 20, "IQ": 20, "HT": 10}
SKILLS = {
    "Stealth": ("DX", -1),
    "Observation": ("IQ", -1),
    "Diplomacy": ("IQ", -2),
    "Survival": ("IQ", -1),
}
TRAITS = {"Keen senses": 5, "Fit": 5, "Curious": -5, "Code of honor": -10}
