"""Reject missing, empty or non-passing browser reports, including silently skipped cases."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED_JOURNEYS: dict[str, tuple[str, ...]] = {
    "browser": (
        "keyboard item controls inspect and restore focus",
        "use retry consumes once and updates the current sheet",
        "first use of the mic discloses browser speech before any microphone starts",
        "reviewed push-to-talk shares the text path and narration is independently interruptible",
        "denied and unsupported microphones retain usable text fallback",
        "recognition network failure preserves partial review without auto-submission",
        "campaign switch clears partial speech and stops private narration",
        "revoked scene access aborts the microphone and erases review buffers",
    ),
    "live": (
        "two identities activate a saved party and review speech through the live director",
        "independent captive and rescuer choices survive reconnect and reunite privately",
    ),
    "reference": (
        "reference adventure: reviewed voice, negotiation, saved epilogue and successor",
        "reference rescue: separate players coordinate, reconnect, reclaim gear and reunite",
    ),
    "startup": (
        "solo production entry, illegal party, stale edit, lost activation, refresh and opening action",
    ),
}


def check(directory: Path) -> list[str]:
    errors: list[str] = []
    for name, required in REQUIRED_JOURNEYS.items():
        path = directory / f"{name}.xml"
        if not path.is_file():
            errors.append(f"Missing {name} browser report")
            continue
        cases = list(ET.parse(path).getroot().iter("testcase"))
        if not cases:
            errors.append(f"Empty {name} browser report")
            continue
        names = {case.get("name") or "" for case in cases}
        for expected in required:
            if not any(expected in actual for actual in names):
                errors.append(f"Missing {name} browser journey: {expected}")
        for case in cases:
            if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
                errors.append(f"Non-passing {name} browser evidence: {case.get('name')}")
    return errors


if __name__ == "__main__":
    failures = check(Path(sys.argv[1]))
    if failures:
        raise SystemExit("\n".join(failures))
